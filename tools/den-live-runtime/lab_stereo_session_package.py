"""Prepare protected producer4/5 packages and opt-in fixed command declarations.

Use python -P tools/den-live-runtime/lab_stereo_session_package.py prepare
--inputs ABS_OWNER_EXPORT_DIRECTORY --output ABS_NEW_PACKAGE_DIRECTORY.
Preparation reads no credentials, starts no source, and calls no host/model.
The separately installed exact-capture association authority remains mandatory.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / 'stack/services/home-context', ROOT / 'stack/services/perception-lab'):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

from journal import Grant, LIVE_PROFILE, digest
from live_ingress import validate_grants
from live_den_preflight import LocalDenPreflight
from lab_live_runtime import lab_graph_configuration, _join_and_configuration, _desk_archive_binding, _assessment_binding
from lab_live_desk_admission import LabLiveDeskAdmission, DEFINITION_KEYS
from lab_stereo_desk_capability import COMMISSIONING_KEYS
from lab_live_desk_anchor import POLICY_KEYS, body_ref, ref, _qualification
from capture_timing import commissioning
from paired_preview_client import encoded, sha
if __package__:
    from .lab_session_package import INPUTS, EVIDENCE, PackageRefusal, _need, _exact, _sha, _path, _no_secrets
else:
    from lab_session_package import INPUTS, EVIDENCE, PackageRefusal, _need, _exact, _sha, _path, _no_secrets

INPUT_KEYS = INPUTS | {'stereo', 'store_window', 'used_originals', 'home_entry', 'command_capture'}
STEREO_KEYS = {'schema', 'version', 'commissioning', 'stereo_policy', 'depth_clock_policy',
               'authority_binding', 'association_originals', 'media'}
AUTHORITY_KEYS = {'scope', 'lab_binding_digest', 'stereo_policy_ref', 'association_method_ref',
                  'projection_ref', 'registration_ref', 'clock_mapping_ref', 'depth_clock_ref',
                  'requirements_ref', 'assessment_report_ref', 'applicability_ref'}
PROVISION_PATHS = ('mount_file', 'bootstrap_file', 'operator_file', 'preflight_file',
                   'commissioning_file', 'qualification_file')
PROVISION_HASHES = ('context_token_sha256', 'operator_token_sha256', 'publication_token_sha256', 'worker_token_sha256')
BLOCKERS = ('concrete_exact_capture_association_authority_not_installed',
            'fresh_runtime_preflight_required', 'source4_worker_home_gate_required',
            'canonical_command_capture_successor_unavailable',
            'retired_session_rebinding_policy_unavailable')


def _bounded(value, maximum, reason):
    try:
        raw = encoded(value)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise PackageRefusal(reason) from None
    _need(len(raw) <= maximum, reason)
    return raw


def _stereo(value, producer, join, now):
    _exact(value, STEREO_KEYS, 'closed_stereo_package_inputs_required')
    bundled = producer['version'] in (6, 7, 8)
    existing = producer['version'] == 5
    stereo_version = 3 if bundled else 2 if existing else 1
    _need(value['schema'] == 'home-den-stereo-package-inputs-' + str(stereo_version)
          and type(value['version']) is int and value['version'] == stereo_version,
          'stereo_package_input_version_required')
    c, p, a = value['commissioning'], value['stereo_policy'], value['authority_binding']
    scope, dp = join.scope, producer['desk_policy']
    if existing:
        _need(dp['version'] == 3 and dp['original_family'] == 'existing-api-assessment-1'
              and _sha(dp['assessment_policy_sha256']), 'fixed_existing_api_policy3_required')
    if bundled:
        _need(dp['version'] == 4 and dp['original_family'] == 'existing-api-bundled-rgb-1'
              and _sha(dp['bundle_input_policy_sha256']), 'fixed_bundled_rgb_policy4_required')
    _exact(c, COMMISSIONING_KEYS, 'complete_fixed_commissioning_originals_required')
    _exact(p, POLICY_KEYS, 'closed_independent_stereo_policy_required')
    _exact(a, AUTHORITY_KEYS, 'fixed_installed_authority_binding_required')
    _need(p['schema'] == 'home-desk-stereo-policy-1' and type(p['version']) is int and p['version'] == 1
          and p['status'] == 'independently_assessed' and p['scope'] == scope
          and body_ref(p) == dp['stereo_policy_ref'], 'exact_stereo_policy_pin_required')
    for key in ('method_ref', 'association_method_ref', 'projection_ref', 'registration_ref', 'clock_mapping_ref',
                'depth_clock_ref', 'requirements_ref', 'assessment_report_ref', 'applicability_ref'):
        ref(p[key])
    _need(all(type(p[k]) in (int,float) and math.isfinite(p[k]) and p[k]>=0 for k in
        ('nose_error_bound_m','max_abs_skew_s','max_depth_age_s','max_speed_m_s','max_accel_m_s2',
         'max_spread_m','valid_from','available_at','valid_until'))
        and 0<p['nose_error_bound_m']<=10 and 0<p['max_abs_skew_s']<=.1 and 0<p['max_depth_age_s']<=3
        and 0<p['max_spread_m']<=10 and type(p['radius_px']) is int and 1<=p['radius_px']<=16
        and type(p['min_support']) is int and 3<=p['min_support']<=256
        and p['valid_from']<=p['available_at']<=now<p['valid_until'], 'current_bounded_stereo_policy_required')
    depth_clock = commissioning(value['depth_clock_policy'])
    expected = dict(scope=deepcopy(scope), lab_binding_digest=join.binding_digest,
        stereo_policy_ref=deepcopy(dp['stereo_policy_ref']), association_method_ref=deepcopy(dp['association_method_ref']),
        projection_ref=body_ref(c['projection']), registration_ref=body_ref(c['registration']),
        clock_mapping_ref={'id': c['clock_mapping']['mapping_id'], 'digest': c['clock_mapping']['mapping_digest']},
        depth_clock_ref=body_ref(depth_clock), **{key: deepcopy(p[key]) for key in
            ('requirements_ref', 'assessment_report_ref', 'applicability_ref')})
    _need(a == expected and all(p[key] == expected[key] for key in
          ('association_method_ref', 'projection_ref', 'registration_ref', 'clock_mapping_ref', 'depth_clock_ref')),
          'authority_and_qualification_pins_must_match')
    # Reuse the actual publisher's independent static calibration rules and
    # the existing owner-local consumer's exact original/assessment checks.
    # These checks establish consistency, not measurement origin or authority.
    from desk_enclosure import _binding, _candidate, _assessment
    _binding(c['binding']); _candidate(c['candidate'], c['binding'])
    _assessment(c['assessment'], c['binding'], c['candidate'], now)
    _need(c['binding']['scope'] == scope and c['binding']['method_ref'] == dp['method_ref']
          and c['binding']['association_method_ref'] == dp['association_method_ref']
          and c['binding']['candidate_digest'] == dp['candidate_digest']
          and c['binding']['assessment_ref'] == dp['assessment_ref']
          and c['candidate']['protection_policy_ref'] == dp['protection_policy_ref']
          and body_ref(c['protection_policy']) == dp['protection_policy_ref'], 'exact_desk_commissioning_pins_required')
    publisher = LabLiveDeskAdmission(producer['live_config'], policy=dp, revision=producer['revision'],
        resolve_inputs=lambda _: None, original_retainer=lambda _: None, clock=lambda: now,
        stereo_media_resolver=lambda _: None,
        **({'expected_assessment_binding': _assessment_binding(scope, dp, join.binding_digest)} if existing else {}),
        **({'expected_bundle_binding': join.bundled_binding()} if bundled else {}))
    publisher._calibration(c)
    _need(c['clock_mapping'] == join.source['capture_clock'], 'original_clock_policy_required')
    _need(c['binding']['registration_ref'] == body_ref(c['registration'])
          and c['binding']['clock_mapping_ref'] == {'id': join.source['capture_clock']['mapping_id'],
              'digest': join.source['capture_clock']['mapping_digest']}
          and c['assessment']['registration']['error_bound_m'] >= c['registration']['error_bound_m'],
          'registration_or_clock_original_mismatch')
    definition=c['protection_policy']
    _exact(definition,DEFINITION_KEYS,'complete_original_protection_definition_required')
    _need(definition['schema']=='home-desk-protection-definition-1' and type(definition['version']) is int
        and definition['version']==1 and definition['body_primitive_id']==dp['body_primitive_id']
        and definition['protection_primitive_id']==dp['protection_primitive_id']
        and definition['required_body_part_ids']==[dp['body_primitive_id']]
        and definition['protected_parts']==c['assessment']['protected_parts']
        and 'head' in definition['protected_parts'], 'complete_original_protection_definition_required')
    originals = value['association_originals']
    _need(isinstance(originals, list) and len(originals) <= producer['limits'][
        'max_capture_originals' if producer['version'] in (7, 8) else 'max_cycles'],
          'bounded_association_original_catalog_required')
    _need(len({sha(encoded(row)) for row in originals}) == len(originals), 'unique_association_originals_required')
    for original in originals:
        _qualification({'schema': 'home-desk-stereo-qualification-1', 'version': 1,
            'stereo_policy': p, 'association': original, 'projection': c['projection'],
            'registration': c['registration'], 'clock_mapping': c['clock_mapping'],
            'depth_clock_policy': depth_clock}, scope=scope, policy_ref=dp['stereo_policy_ref'], now=now, **({'clock_unit': 'unix_s'} if bundled else {}))
    media = value['media']
    _exact(media, {'directory', 'max_objects', 'max_bytes', 'min_free_bytes'}, 'fixed_media_configuration_required')
    _need(_path(media['directory']) and type(media['max_objects']) is int and 1 <= media['max_objects'] <= 128
          and type(media['max_bytes']) is int and 1024 <= media['max_bytes'] <= 128 * 1024**2
          and type(media['min_free_bytes']) is int and media['min_free_bytes'] >= 1, 'bounded_stereo_media_configuration_required')
    _bounded(c, 512 * 1024 - 1, 'bounded_commissioning_originals_required')
    qualification = {'stereo_policy': deepcopy(p), 'depth_clock_policy': deepcopy(depth_clock),
        'authority_binding': deepcopy(a), 'association_originals': deepcopy(originals)}
    _bounded(qualification, 2 * 1024**2 - 1, 'bounded_qualification_originals_required')
    media_binding = {'scope': deepcopy(scope), 'lab_binding_digest': join.binding_digest,
        'media_generation': producer['lab_binding']['media_generation'], 'camera_id': c['registration']['camera_id']}
    if existing:
        media_binding.update(original_family=dp['original_family'], assessment_policy_sha256=dp['assessment_policy_sha256'])
    if bundled:
        media_binding.update(original_family=dp['original_family'], bundle_input_policy_sha256=dp['bundle_input_policy_sha256'])
    return c, qualification, media_binding


def _used_export(value, producer, join, media_binding):
    report = {'supplied': value is not None, 'content_integrity_complete': False,
              'source_authenticity_verified': False, 'ordinary_replay_complete': False}
    if value is None:
        return report
    _exact(value, {'schema', 'version', 'binding', 'records', 'expected_digests'}, 'closed_used_original_export_required')
    _need(value['schema'] == 'home-lab-used-original-export-1' and type(value['version']) is int
          and value['version'] == 1, 'used_original_export_version_required')
    from lab_original_archive import original_digest, semantic_archive_binding
    expected = {'capture': {'session_id': join.scope['session_id'], 'method_ref': join.method, 'model_ref': join.model},
        'semantic': semantic_archive_binding(join.scope, producer['semantic_policy']),
        'desk': _desk_archive_binding(join.scope, producer['desk_policy'], join.binding_digest), 'media': media_binding}
    _need(value['binding'] == expected and isinstance(value['records'], list)
          and 0 < len(value['records']) <= producer['original_archive']['max_records']
          and isinstance(value['expected_digests'], list) and all(_sha(v) for v in value['expected_digests']),
          'exact_used_original_binding_required')
    actual = [original_digest(record) for record in value['records']]
    _need(len(set(actual)) == len(actual) and len(set(value['expected_digests'])) == len(value['expected_digests'])
          and set(actual) == set(value['expected_digests']), 'used_original_export_missing_or_changed_body')
    _bounded(value, producer['original_archive']['max_bytes'], 'used_original_export_byte_limit')
    return {**report, 'content_integrity_complete': True, 'records': len(actual), 'body_digest': sha(encoded(value))}


def _store_export(value, session):
    report = {'supplied': value is not None, 'ordered_originals_intact': False,
              'window_completeness_verified': False, 'whole_store_replay_complete': False}
    if value is None:
        return report
    _exact(value, {'schema', 'version', 'session_id', 'after_id', 'through_id', 'events', 'export_receipt'},
           'closed_whole_store_export_required')
    _need(value['schema'] == 'home-lab-store-window-export-1' and type(value['version']) is int and value['version'] == 1
          and value['session_id'] == session['id'] and type(value['after_id']) is int
          and type(value['through_id']) is int and 0 <= value['after_id'] <= value['through_id']
          and isinstance(value['events'], list) and len(value['events']) <= 4096
          and (value['export_receipt'] is None or isinstance(value['export_receipt'], dict)), 'fixed_whole_store_window_required')
    previous = value['after_id']
    for event in value['events']:
        _need(isinstance(event, dict) and type(event.get('id')) is int and previous < event['id'] <= value['through_id']
              and event.get('session_id') == session['id'] and isinstance(event.get('kind'), str)
              and isinstance(event.get('payload'), dict), 'whole_store_original_order_or_scope_refused')
        previous = event['id']
    _bounded(value, 8 * 1024**2, 'whole_store_export_byte_limit')
    # Global Store IDs can interleave sessions. Ordered rows and an owner's
    # receipt alone cannot prove that no matching event was omitted.
    return {**report, 'ordered_originals_intact': True, 'events': len(value['events']),
            'after_id': value['after_id'], 'through_id': value['through_id'], 'body_digest': sha(encoded(value))}


def prepare_stereo_session_package(artifacts, *, now):
    """Explicit pure profile dispatch; output JSON never supplies capabilities."""
    result = {'schema': 'home-den-lab-session-package-2', 'version': 2, 'status': 'refused',
        'activation_ready': False, 'physical_authority': False, 'capability_constructed': False,
        'missing_dependencies': [], 'activation_blockers': list(BLOCKERS), 'files': {}}
    try:
        _exact(artifacts, INPUT_KEYS, 'closed_stereo_owner_inputs_required')
        assessed_commands = artifacts['schema'] == 'home-den-lab-session-inputs-7'
        poll_budget = assessed_commands or artifacts['schema'] == 'home-den-lab-session-inputs-6'
        bundled = poll_budget or artifacts['schema'] == 'home-den-lab-session-inputs-5'
        commands = assessed_commands or artifacts['schema'] == 'home-den-lab-session-inputs-4' or bundled and not poll_budget and artifacts['command_capture'] is not None
        existing = artifacts['schema'] in {'home-den-lab-session-inputs-3', 'home-den-lab-session-inputs-4'}
        input_version = 7 if assessed_commands else 6 if poll_budget else 5 if bundled else 4 if commands else 3 if existing else 2
        _need(artifacts['schema'] == 'home-den-lab-session-inputs-' + str(input_version)
              and type(artifacts['version']) is int and artifacts['version'] == input_version
              and type(now) in (int, float) and math.isfinite(now), 'stereo_owner_input_version_required')
        if existing:
            package_version = 4 if commands else 3
            replacements = {'source4_worker_home_gate_required': 'source5_mounted_worker_home_gate_required',
                'canonical_command_capture_successor_unavailable':
                    'fixed_command_owner_runtime_ready_required' if commands else 'target_compatible_command_owner_not_installed'}
            result.update(schema='home-den-lab-session-package-' + str(package_version), version=package_version,
                activation_blockers=[replacements.get(b,b) for b in BLOCKERS])
        if bundled:
            replacements = {'source4_worker_home_gate_required': 'source6_mounted_worker_home_gate_required',
                'canonical_command_capture_successor_unavailable':
                    'source6_command_native_gate_required' if commands else 'source6_command_declaration_required'}
            result.update(schema='home-den-lab-session-package-5', version=5,
                activation_blockers=[replacements.get(b,b) for b in BLOCKERS])
        if poll_budget and not assessed_commands:
            _need(artifacts['command_capture'] is None, 'poll_budget_command_profile_unavailable')
            result.update(schema='home-den-lab-session-package-6', version=6,
                activation_blockers=['source7_mounted_worker_home_gate_required' if b == 'source6_mounted_worker_home_gate_required'
                    else 'poll_budget_command_profile_unavailable' if b == 'source6_command_declaration_required' else b
                    for b in result['activation_blockers']] + ['operational_cadence_and_resource_validation_required'])
        if assessed_commands:
            _need(artifacts['command_capture'] is not None, 'assessed_command_declaration3_required')
            replacements = {'source6_mounted_worker_home_gate_required': 'source8_mounted_worker_home_gate_required',
                'source6_command_native_gate_required': 'source8_retained_task_command_gate_required'}
            result.update(schema='home-den-lab-session-package-7', version=7,
                activation_blockers=[replacements.get(b, b) for b in result['activation_blockers']] +
                    ['spotlight_semantic_CCT_mapping_unavailable', 'operational_cadence_and_resource_validation_required'])
        _no_secrets(artifacts); _bounded(artifacts, 24 * 1024**2, 'bounded_private_package_inputs_required')
        a = deepcopy(artifacts); p, session, worker = a['producer'], a['session'], a['worker_config']
        producer_version = 8 if assessed_commands else 7 if poll_budget else 6 if bundled else 5 if existing else 4
        live_version = 13 if assessed_commands else 12
        _need(p.get('schema') == 'home-incumbent-lab-producer-' + str(producer_version)
              and type(p.get('version')) is int and p['version'] == producer_version
              and p.get('live_config', {}).get('schema') == 'home-live-ingress-config-' + str(live_version)
              and p['live_config'].get('version') == live_version and session.get('state') == 'recording',
              'exact_current_producer8_required' if assessed_commands else 'exact_current_producer6_required' if bundled else 'exact_current_producer5_required' if existing else 'exact_current_producer4_required')
        join, graph = _join_and_configuration(p, session)
        _need(graph == lab_graph_configuration(p, session=session), 'fixed_graph_configuration_required')
        live, scope = p['live_config'], graph['session_ref']
        grants = [Grant.from_dict(g, profile=LIVE_PROFILE) for g in p['journal_grants']]
        validate_grants(grants, live)
        _need(len({g.token_sha256 for g in grants}) == len(grants), 'distinct_journal_credentials_required')
        expected_worker = {k: deepcopy(v) for k, v in live['manifest']['worker'].items()
                           if k != 'config_digest' and not k.endswith('schema_pins')}
        expected_worker.update(profile=LIVE_PROFILE, identity=scope, manifest_ref=live['manifest_ref'], live_manifest=live['manifest'])
        _need(isinstance(worker, dict) and _path(worker.get('token_file'))
              and {k: v for k, v in worker.items() if k != 'token_file'} == expected_worker, 'exact_source4_worker_required')
        runtime, provision, operator = (a[k] for k in ('runtime', 'provisioning', 'operator'))
        budget_keys = ('max_poll_attempts', 'max_capture_originals') if poll_budget else ('max_cycles',)
        _exact(runtime, {'node_runtime', 'archive_file', 'archive_limits', 'source_bundle_digest',
            *budget_keys, 'max_runtime_s', 'interval_s', 'drain_timeout_s'}, 'closed_runtime_options_required')
        _need(_path(runtime['node_runtime']) and _path(runtime['archive_file']) and _sha(runtime['source_bundle_digest']),
              'fixed_runtime_source_paths_required')
        _exact(runtime['archive_limits'], {'max_records', 'max_bytes', 'min_free_bytes'}, 'bounded_recording_limits_required')
        _need(all(type(v) is int and v > 0 for v in runtime['archive_limits'].values())
              and runtime['archive_limits']['max_records'] <= 100000
              and runtime['archive_limits']['max_bytes'] <= 64 * 1024**2, 'bounded_recording_limits_required')
        runtime_version = 3 if assessed_commands else 2 if poll_budget else 1
        sc = dict(schema='home-live-session-config-' + str(runtime_version), version=runtime_version,
            session_ref=scope, configuration_digest=digest(graph), mode='live_shadow_unqualified',
            **{k: runtime[k] for k in (*budget_keys, 'max_runtime_s', 'interval_s', 'drain_timeout_s')})
        from live_den_mount import _runtime_module
        _runtime_module()
        from home_den_runtime.supervisor import SessionSupervisor
        def forbidden(*_args, **_kwargs): raise AssertionError('package_preparation_is_inert')
        SessionSupervisor(sc, graph_factory=forbidden, recorder_factory=forbidden, worker_factory=forbidden, preflight=forbidden)
        budget_matches = (sc['max_poll_attempts'] <= p['limits']['max_poll_attempts']
            and sc['max_capture_originals'] == p['limits']['max_capture_originals']) if poll_budget else sc['max_cycles'] <= p['limits']['max_cycles']
        _need(budget_matches and sc['max_runtime_s'] <= p['limits']['max_runtime_s'],
              'producer_runtime_limits_must_match')
        _exact(provision, {*PROVISION_PATHS, *PROVISION_HASHES, 'lab_token_sha256'}, 'closed_stereo_provisioning_required')
        _need(all(_path(provision[k]) for k in PROVISION_PATHS), 'absolute_protected_provision_paths_required')
        hashes = [provision[k] for k in PROVISION_HASHES]
        _need(all(_sha(h) for h in hashes) and len(set(hashes)) == 4 and isinstance(provision['lab_token_sha256'], list)
              and all(_sha(h) for h in provision['lab_token_sha256']) and not set(hashes) & set(provision['lab_token_sha256']),
              'independent_lab_context_operator_worker_credentials_required')
        by_hash = {g.token_sha256: g for g in grants}
        _need(provision['operator_token_sha256'] not in by_hash and all(provision[k] in by_hash for k in
            ('context_token_sha256', 'publication_token_sha256', 'worker_token_sha256')), 'fixed_journal_grant_credentials_required')
        context = by_hash[provision['context_token_sha256']]
        context_raw = next(g for g in p['journal_grants'] if g['token_sha256'] == context.token_sha256)
        _need(context.operations == frozenset({'read', 'intent'})
              and context.scopes == frozenset({(scope['site_id'], scope['room_id'])})
              and sorted(context_raw['live_bindings'], key=lambda b: b['source_id']) ==
                [{'manifest_ref': live['manifest_ref'], 'source_id': sid, 'capabilities': ['read']} for sid in sorted(live['manifest']['sources'])]
              and 'event' in by_hash[provision['publication_token_sha256']].operations
              and by_hash[provision['worker_token_sha256']].operations == frozenset({'read', 'worker_commit'}),
              'exact_read_intent_publication_worker_grants_required')
        from urllib.parse import urlsplit
        _exact(operator, {'upstream', 'ca_file', 'token_file', 'timeout_s'}, 'fixed_existing_operator_required')
        origin = urlsplit(operator['upstream'])
        _need(origin.scheme == 'https' and origin.hostname and origin.path in ('', '/')
              and not (origin.username or origin.password or origin.query or origin.fragment)
              and all(_path(operator[k]) for k in ('ca_file', 'token_file'))
              and type(operator['timeout_s']) in (int, float) and math.isfinite(operator['timeout_s'])
              and 0 < operator['timeout_s'] <= 12, 'fixed_existing_tls_operator_required')
        c, qualification, media_binding = _stereo(a['stereo'], p, join, now)
        command_capture = None
        if commands:
            # This validates a protected declaration, not an installed factory
            # or available native artifact. Only runtime code selects a client.
            from room_command_factory import validate_command_declaration
            try:
                command_capture = validate_command_declaration(a['command_capture'],
                    source_bundle_digest=runtime['source_bundle_digest'])
            except ValueError as error:
                raise PackageRefusal(str(error)) from None
            _need(command_capture['version'] == (3 if assessed_commands else 2 if bundled else 1), 'exact_command_source_profile_required')
            if bundled:
                _need(command_capture['original_family'] == p['desk_policy']['original_family']
                      and command_capture['bundle_input_policy_sha256'] == p['desk_policy']['bundle_input_policy_sha256'],
                      'same_bundled_command_policy_required')
            limits = command_capture['limits']
            _need(limits['max_runtime_s'] <= runtime['max_runtime_s']
                  and (assessed_commands or limits['max_commits'] <= runtime['max_cycles'])
                  and .02 <= limits['interval_s'] <= 1, 'command_lifecycle_exceeds_parent_runtime')
            _bounded(command_capture, 128 * 1024 - 1, 'bounded_command_declaration_required')
        paths = [provision[k] for k in PROVISION_PATHS] + [p['journal_path'], p['publication_token_file'],
            p['original_archive']['filename'], runtime['archive_file'], worker['token_file'], operator['token_file'], a['stereo']['media']['directory']]
        if commands:
            paths += [command_capture['recording'][key] for key in ('native_file','canonical_file','provenance_file')]
        _need(len({os.path.normcase(os.path.normpath(path)) for path in paths}) == len(paths), 'distinct_private_state_and_credential_paths_required')
        _exact(a['evidence'], EVIDENCE, 'complete_owner_evidence_roles_required')
        _need(all(a['evidence'][k] is not None and a['evidence'][k] != [] for k in EVIDENCE)
              and a['evidence']['registration'] == worker['room_policy']['registration']
              and a['evidence']['fixtures'] == worker['room_policy']['fixtures']
              and a['evidence']['examples'] == worker['scene_policy']['catalog'], 'exact_room_fixture_example_originals_required')
        LocalDenPreflight(provision['preflight_file'], configuration_digest=digest(graph),
            control_policy=join.source['control_clock'], archive_directory=str(Path(runtime['archive_file']).parent),
            clock=lambda: now, reader=lambda _: a['preflight'], probe=forbidden).receipt()
        used_report = _used_export(a['used_originals'], p, join, media_binding)
        window_report = _store_export(a['store_window'], session)
        if not commands:
            _need(a['command_capture'] is None, 'canonical_command_capture_successor_not_available')
        operator_config = {'schema': 'home-live-operator-config-1', 'version': 1, **deepcopy(operator),
                           'session_ref': deepcopy(scope), 'configuration_digest': digest(graph)}
        runtime_config = dict(schema='home-live-runtime-config-' + str(runtime_version), version=runtime_version, session=sc, worker_config=worker,
            **{k: runtime[k] for k in ('node_runtime', 'archive_file', 'archive_limits', 'source_bundle_digest')})
        source_version = 5 if assessed_commands else 4 if poll_budget else 3 if bundled else 2 if existing else 1
        stereo_source = dict(schema='home-lab-stereo-source-config-' + str(source_version), version=source_version,
            commissioning_file=provision['commissioning_file'], commissioning_digest=sha(encoded(c)),
            qualification_file=provision['qualification_file'], qualification_digest=sha(encoded(qualification)),
            qualification_authority_ref={'id': qualification['authority_binding']['association_method_ref']['id'],
                                         'digest': sha(encoded(qualification['authority_binding']))},
            media=deepcopy(a['stereo']['media']))
        if existing:
            stereo_source.update(original_family=p['desk_policy']['original_family'],
                assessment_policy_sha256=p['desk_policy']['assessment_policy_sha256'])
        if bundled:
            stereo_source.update(original_family=p['desk_policy']['original_family'],
                bundle_input_policy_sha256=p['desk_policy']['bundle_input_policy_sha256'])
        mount_version, bootstrap_version = (8, 7) if assessed_commands else (7, 6) if poll_budget else (6, 5) if bundled else (5, 4) if commands else (4, 3) if existing else (3, 2)
        mount = dict(schema='home-managed-den-mount-' + str(mount_version), version=mount_version, producer=p, runtime=runtime_config,
            preflight_file=provision['preflight_file'], stereo_source=stereo_source,
            **{k: provision[k] for k in ('context_token_sha256', 'operator_token_sha256')})
        bootstrap = dict(schema='home-den-lab-bootstrap-' + str(bootstrap_version), version=bootstrap_version, mount_file=provision['mount_file'],
            operator_token_sha256=provision['operator_token_sha256'], scope={k: scope[k] for k in ('site_id', 'room_id')})
        if commands or bundled:
            mount['command_capture'] = deepcopy(command_capture)
            bootstrap['command_capture_digest'] = sha(encoded(command_capture))
        # The fixed file writer adds one LF. Match the protected readers' byte
        # caps before calling the declaration prepared, including that byte.
        _bounded(mount, 2 * 1024**2 - 1, 'bounded_mount5_file_required' if commands else 'bounded_mount3_file_required')
        _bounded(bootstrap, 65536 - 1, 'bounded_bootstrap4_file_required' if commands else 'bounded_bootstrap2_file_required')
        files = {'mount.json': mount, 'bootstrap.json': bootstrap, 'operator.json': operator_config,
            'lab-session.json': session, 'preflight.json': a['preflight'], 'evidence.json': a['evidence'],
            'stereo-commissioning.json': c, 'stereo-qualification.json': qualification}
        for key, filename in (('used_originals','used-originals.json'), ('store_window','whole-store-window.json')):
            if a[key] is not None: files[filename] = a[key]
        blockers = list(result['activation_blockers'])
        home = a['home_entry']
        if home is None:
            blockers.append('exact_home_entry_export_required')
        else:
            _need(isinstance(home, dict) and home.get('schema') == 'home-live-lab-entry-1' and home.get('version') == 1
                  and home.get('operator') == operator_config and home.get('options', {}).get('qualification') == 'live_shadow_unqualified'
                  and all(home['options'].get(k) == v for k,v in scope.items())
                  and home['options'].get('resident_binding',{}).get('schema') ==
                      ('home-live-resident-binding-12' if assessed_commands else 'home-live-resident-binding-11')
                  and (not assessed_commands or type(home['options']['resident_binding'].get('version')) is int
                      and home['options']['resident_binding']['version'] == 12)
                  and home['options']['resident_binding'].get('manifest_ref') == live['manifest_ref']
                  and home['options']['resident_binding'].get('live_manifest') == live['manifest']
                  and home.get('bridge',{}).get('schema') == 'home-live-context-bridge-3'
                  and home['bridge'].get('binding') == {'scope':scope,'manifest_ref':live['manifest_ref']},
                  'exact_existing_home_bridge_operator_binding_required')
            files['home-entry.json'] = home
            blockers.append('existing_home_JS_validator_and_asset_gate_required')
        blockers.append('whole_store_window_completeness_not_verified')
        bindings = dict(scope=scope, graph_configuration_digest=digest(graph), lab_binding_digest=join.binding_digest,
            source_bundle_digest=runtime['source_bundle_digest'], producer_digest=sha(encoded(p)),
            manifest_ref=deepcopy(live['manifest_ref']), grants_digest=sha(encoded(p['journal_grants'])),
            qualification_authority_ref=stereo_source['qualification_authority_ref'],
            media_binding=media_binding, media_binding_digest=sha(encoded(media_binding)),
            original_archive_binding={'capture':{'session_id':scope['session_id'],'method_ref':join.method,'model_ref':join.model},
                'desk':_desk_archive_binding(scope,p['desk_policy'],join.binding_digest)},
            preflight_validation='supplied_record_shape_only_no_probe', target_files={k:provision[k] for k in PROVISION_PATHS},
            input_digest=sha(encoded(a)), file_digests={k:sha(encoded(v)) for k,v in files.items()},
            used_originals=used_report, whole_store_window=window_report)
        if commands or bundled:
            bindings['command_capture_digest'] = bootstrap['command_capture_digest']
        if bundled:
            bindings['capture_clock_policy'] = deepcopy(join.source['capture_clock'])
        result.update(status='prepared', activation_blockers=blockers, bindings=bindings, files=files,
                      package_digest=sha(encoded(bindings)))
    except PackageRefusal as error:
        result['missing_dependencies']=[str(error)]
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError, RecursionError):
        result['missing_dependencies']=[('source8_or_independent_original_validator_refused' if result['version'] == 7 else 'source6_or_independent_original_validator_refused'
            if result['version'] in (5, 6) else 'source5_or_independent_original_validator_refused'
            if result['version'] in (3, 4) else 'source4_or_independent_original_validator_refused')]
    return deepcopy(result)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['prepare']);parser.add_argument('--inputs',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--now',type=float)
    args=parser.parse_args(argv);directory,output=Path(args.inputs),Path(args.output)
    source=directory/'inputs.json'
    _need(directory.is_absolute() and output.is_absolute() and not directory.is_symlink()
          and not source.is_symlink() and source.is_file() and source.stat().st_size<=24*1024**2,
          'bounded_private_owner_export_required')
    _need(sys.platform=='win32' or source.stat().st_mode&0o077==0,'private_owner_export_required')
    raw=source.read_bytes();_need(len(raw)<=24*1024**2,'bounded_private_owner_export_required')
    result=prepare_stereo_session_package(json.loads(raw),now=time.time() if args.now is None else args.now)
    receipt={k:v for k,v in result.items() if k!='files'};receipt['original_input_file_sha256']=sha(raw)
    output.mkdir(mode=0o700,parents=False,exist_ok=False)
    for name,body in {**result['files'],'package.json':receipt}.items():
        descriptor=os.open(output/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_BINARY',0),0o600)
        with os.fdopen(descriptor,'wb') as handle:
            handle.write(encoded(body)+b'\n');handle.flush();os.fsync(handle.fileno())
    print(json.dumps({'status':result['status'],'activation_ready':False,
        'missing_dependencies':result['missing_dependencies'],'activation_blockers':result['activation_blockers']}))
    return 0 if result['status']=='prepared' else 2


if __name__=='__main__':raise SystemExit(main())
