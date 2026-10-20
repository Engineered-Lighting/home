"""Fixed protected stereo composition; JSON never supplies a runtime provider.

Construction borrows the incumbent Store and opens no camera, journal, binary
spool, worker or network connection. A separately installed qualified association
authority remains required; this module cannot create one from survey records.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat

from lab_live_acquisition import utc_now
from paired_preview_client import encoded, sha

SOURCE_KEYS = {'schema', 'version', 'commissioning_file', 'commissioning_digest',
               'qualification_file', 'qualification_digest', 'qualification_authority_ref', 'media'}
EXISTING_SOURCE_KEYS = SOURCE_KEYS | {'original_family', 'assessment_policy_sha256'}
BUNDLED_SOURCE_KEYS = SOURCE_KEYS | {'original_family', 'bundle_input_policy_sha256'}
QUALIFICATION_KEYS = {'stereo_policy', 'depth_clock_policy', 'authority_binding', 'association_originals'}


def _need(condition, reason):
    if not condition:
        raise ValueError(reason)


def read_private_record(filename, *, maximum):
    """Explicit successor bound for protected mount3/qualification records only."""
    path = Path(filename)
    _need(path.is_absolute() and not path.is_symlink() and type(maximum) is int
          and 1 <= maximum <= 2 * 1024**2, 'bounded_private_stereo_file_required')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    try:
        info = os.fstat(fd)
        _need(stat.S_ISREG(info.st_mode) and hasattr(os, 'geteuid')
              and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0
              and 0 < info.st_size <= maximum, 'private_stereo_owner_file_required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        _need(0 < len(raw) <= maximum, 'private_stereo_file_exceeds_bound')
    finally:
        os.close(fd)
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result, 'private_stereo_duplicate_key')
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError('private_stereo_finite_json_required')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def read_stereo_mount(filename, *, version=3):
    _need(type(version) is int and version in (3, 4, 5, 6, 7, 8), 'protected_stereo_mount_version_required')
    value = read_private_record(filename, maximum=2 * 1024**2)
    _need(isinstance(value, dict) and value.get('schema') == 'home-managed-den-mount-' + str(version)
          and type(value.get('version')) is int and value['version'] == version,
          'protected_stereo_mount' + str(version) + '_required')
    return value


def stereo_runtime_composition(producer, source, *, store, preflight, control_clock,
                               qualification_authority, incumbent_runtime=None, clock=None):
    """Assemble actual stereo ports without admitting a source or ticking.

    `qualification_authority` is an installed server capability, never parsed
    from a file, request, module name or URL. Its producer owns the independent
    association method. Missing capability refuses instead of trusting a label.
    """
    from lab_live_runtime import LabRuntimeComposition, _join_and_configuration
    from lab_live_desk_anchor import LabLiveDeskAnchor, _qualification
    from lab_stereo_desk_capability import LabStereoDeskCapability
    _need(qualification_authority is not None and not isinstance(qualification_authority, dict)
          and callable(getattr(qualification_authority, 'binding', None))
          and callable(getattr(qualification_authority, 'resolve', None)), 'installed_stereo_association_authority_required')
    assessed_commands = isinstance(source, dict) and source.get('schema') == 'home-lab-stereo-source-config-5'
    poll_budget = assessed_commands or isinstance(source, dict) and source.get('schema') == 'home-lab-stereo-source-config-4'
    bundled = poll_budget or isinstance(source, dict) and source.get('schema') == 'home-lab-stereo-source-config-3'
    existing = isinstance(source, dict) and source.get('schema') == 'home-lab-stereo-source-config-2'
    source_version, producer_version = (5, 8) if assessed_commands else (4, 7) if poll_budget else (3, 6) if bundled else (2, 5) if existing else (1, 4)
    _need(isinstance(source, dict) and set(source) == (BUNDLED_SOURCE_KEYS if bundled else EXISTING_SOURCE_KEYS if existing else SOURCE_KEYS)
          and source['schema'] == 'home-lab-stereo-source-config-' + str(source_version)
          and type(source['version']) is int and source['version'] == source_version
          and producer.get('schema') == 'home-incumbent-lab-producer-' + str(producer_version)
          and type(producer.get('version')) is int and producer['version'] == producer_version,
          'closed_stereo_source' + str(producer_version) + '_required')
    if bundled:
        policy = producer.get('desk_policy', {})
        _need(policy.get('version') == 4 and source['original_family'] == 'existing-api-bundled-rgb-1'
              and all(source[key] == policy.get(key) for key in ('original_family', 'bundle_input_policy_sha256')),
              'fixed_bundled_rgb_source8_policy_required' if assessed_commands else
              'fixed_bundled_rgb_source7_policy_required' if poll_budget else 'fixed_bundled_rgb_source6_policy_required')
        from service_runtime import ServiceRuntime
        from bundle_rgb_input import BundledRgbInputFactory, BundledRgbInput
        _need(type(incumbent_runtime) is ServiceRuntime and incumbent_runtime.store is store,
              'actual_incumbent_bundled_runtime_required')
        sid = producer['lab_binding']['session_id']
        initial_session = store.get_session(sid)
        camera_id = initial_session['camera_id']
        bundle_factory = incumbent_runtime.bundled_rgb_sources.get(camera_id)
        bundle_input = incumbent_runtime.bundled_rgb_sessions.get(sid)
        runner = incumbent_runtime.runners.get(sid)
        pump = incumbent_runtime.preview_pumps.get(sid)
        def incumbent_current():
            # Cheap local capability/Store checks; no health process or remote I/O
            # while command_current holds the sole Journal lock.
            session = store.get_session(sid)
            _need(not incumbent_runtime.closed and incumbent_runtime.store is store
                  and type(bundle_factory) is BundledRgbInputFactory and type(bundle_input) is BundledRgbInput
                  and incumbent_runtime.bundled_rgb_sources.get(camera_id) is bundle_factory
                  and incumbent_runtime.bundled_rgb_sessions.get(sid) is bundle_input
                  and bundle_factory.store is bundle_input.store is store
                  and bundle_factory.runtime is bundle_input.runtime is incumbent_runtime
                  and bundle_input.session_id == sid and bundle_input.site_id == session['site_id']
                  and bundle_input.camera_id == camera_id == session['camera_id']
                  and bundle_factory.site_id == session['site_id'] and bundle_factory.camera_id == camera_id
                  and bundle_input.policy_sha256 == sha(encoded(bundle_input.policy))
                      == sha(encoded(bundle_factory.policy)) == source['bundle_input_policy_sha256']
                  and not bundle_input.stopped.is_set() and not bundle_input.incomplete
                  and session['state'] == 'recording'
                  and session['generation'] == producer['lab_binding']['media_generation']
                  and incumbent_runtime.runners.get(sid) is runner and runner is not None and not runner.closed
                  and runner.generation == producer['lab_binding']['model_generation']
                  and incumbent_runtime.preview_pumps.get(sid) is pump and pump is not None and not pump.stopped.is_set(),
                  'current_bundled_rgb_runtime_required')
            return True
        incumbent_current()
    elif existing:
        policy = producer.get('desk_policy', {})
        _need(policy.get('version') == 3 and source['original_family'] == 'existing-api-assessment-1'
              and all(source[key] == policy.get(key) for key in ('original_family', 'assessment_policy_sha256')),
              'fixed_existing_api_assessment_policy_required')
        from service_runtime import ServiceRuntime
        from assessment_existing_api import AssessmentExistingApiTap
        _need(isinstance(incumbent_runtime, ServiceRuntime) and incumbent_runtime.store is store,
              'actual_incumbent_assessment_runtime_required')
        assessment_tap = getattr(incumbent_runtime, 'existing_api_assessment', None)
        def incumbent_current():
            _need(not incumbent_runtime.closed and incumbent_runtime.store is store
                  and isinstance(assessment_tap, AssessmentExistingApiTap)
                  and assessment_tap.store is store and assessment_tap.runtime is incumbent_runtime
                  and getattr(incumbent_runtime, 'existing_api_assessment', None) is assessment_tap
                  and assessment_tap.policy_sha256 == source['assessment_policy_sha256']
                  and sha(encoded(assessment_tap.policy)) == source['assessment_policy_sha256'],
                  'current_existing_api_assessment_runtime_required')
            return True
        incumbent_current()
    else:
        _need(incumbent_runtime is None, 'unexpected_existing_api_runtime')
    clock = utc_now if clock is None else clock
    _need(callable(clock), 'fixed_stereo_clock_required')
    source = deepcopy(source)
    commissioning = read_private_record(source['commissioning_file'], maximum=512 * 1024)
    qualified = read_private_record(source['qualification_file'], maximum=2 * 1024**2)
    _need(isinstance(qualified, dict) and set(qualified) == QUALIFICATION_KEYS
          and sha(encoded(commissioning)) == source['commissioning_digest']
          and sha(encoded(qualified)) == source['qualification_digest'], 'stereo_original_digest_mismatch')
    join, _ = _join_and_configuration(producer, store.get_session(producer['lab_binding']['session_id']))
    from live_den_mount import _runtime_module
    _runtime_module()
    from home_den_runtime.lab_stereo_session_package import _stereo
    from home_den_runtime.lab_stereo_media import LabStereoMedia
    # Polling changes admission budgets, never the bundled original proof.
    proof_version = 3 if bundled else source_version
    fixed = {'schema': 'home-den-stereo-package-inputs-' + str(proof_version), 'version': proof_version,
             'commissioning': commissioning, 'media': source['media'], **qualified}
    _, static, media_binding = _stereo(fixed, producer, join, clock())
    expected = deepcopy(static['authority_binding'])
    authority_ref = {'id': expected['association_method_ref']['id'], 'digest': sha(encoded(expected))}
    _need(source['qualification_authority_ref'] == authority_ref
          and qualification_authority.binding() == expected, 'fixed_stereo_association_binding_required')
    if existing:
        _need(assessment_tap.site_id == join.scope['site_id']
              and assessment_tap.camera_id == commissioning['registration']['camera_id'],
              'same_existing_api_assessment_camera_required')
    if bundled:
        _need(bundle_input.site_id == join.scope['site_id']
              and bundle_input.camera_id == commissioning['registration']['camera_id'],
              'same_bundled_rgb_camera_required')
    holder = {}
    def current():
        _need('composition' in holder, 'stereo_composition_not_bound')
        holder['composition'].require_current()
        _need(qualification_authority.binding() == expected, 'stereo_association_binding_changed')
        return True
    def resolve(descriptor):
        current()
        result = qualification_authority.resolve(deepcopy(descriptor))
        current()  # Capability I/O cannot preserve a retired source or expired support.
        _need(isinstance(result, dict) and set(result) == {'schema', 'version', 'stereo_policy', 'association',
              'projection', 'registration', 'clock_mapping', 'depth_clock_policy'}, 'complete_stereo_qualification_required')
        _need(all(result[key] == body for key, body in {
            'stereo_policy': static['stereo_policy'], 'depth_clock_policy': static['depth_clock_policy'],
            'projection': commissioning['projection'], 'registration': commissioning['registration'],
            'clock_mapping': commissioning['clock_mapping']}.items()), 'stereo_qualification_originals_changed')
        _qualification(result, scope=join.scope, policy_ref=producer['desk_policy']['stereo_policy_ref'], now=clock(),
            **({'clock_unit': 'unix_s'} if bundled else {}))
        return deepcopy(result)
    media = LabStereoMedia(source['media']['directory'], store=store, scope=join.scope,
        lab_binding_digest=join.binding_digest, media_generation=producer['lab_binding']['media_generation'],
        current=current, **{key: source['media'][key] for key in ('max_objects', 'max_bytes', 'min_free_bytes')},
        **({key: source[key] for key in ('original_family', 'assessment_policy_sha256')} if existing else {}),
        **({key: source[key] for key in ('original_family', 'bundle_input_policy_sha256')} if bundled else {}))
    _need(media.binding == media_binding, 'fixed_stereo_media_binding_required')
    options = {}
    if existing or bundled:
        from depth_clock import DepthClock
        depth_clock = DepthClock(store, site_id=join.scope['site_id'],
            camera_id=commissioning['registration']['camera_id'], clock=static['depth_clock_policy'],
            calibration=commissioning['projection'])
        if bundled:
            from lab_bundled_rgb_stereo import BundledRgbStereoSource
            options['bundled_rgb_source'] = BundledRgbStereoSource(store,
                expected_bundle_binding=join.bundled_binding(), incumbent_runtime=incumbent_runtime,
                capture_clock_policy=join.source['capture_clock'], depth_clock=depth_clock,
                current=lambda _: current(), clock=clock)
            options['capture_clock_policy'] = deepcopy(join.source['capture_clock'])
        else:
            from lab_existing_api_stereo import ExistingApiStereoSource
            from lab_live_runtime import _assessment_binding
            options['existing_api_source'] = ExistingApiStereoSource(store,
                expected_assessment_binding=_assessment_binding(join.scope, producer['desk_policy'], join.binding_digest),
                assessment_tap=assessment_tap, depth_clock=depth_clock, current=lambda _: current(), clock=clock)
    anchor = LabLiveDeskAnchor(store, scope=join.scope, lab_binding_digest=join.binding_digest,
        stereo_policy_ref=producer['desk_policy']['stereo_policy_ref'], resolve_qualification=resolve,
        retain_media=media.retain_media, resolve_media=media.resolve_retained, clock=clock, **options)
    capability = LabStereoDeskCapability(anchor_source=anchor, commissioning=commissioning,
        policy=producer['desk_policy'], lab_binding_digest=join.binding_digest, media_archive=media, clock=clock)
    def guarded_preflight():
        incumbent_current()
        return preflight()
    composition = LabRuntimeComposition(producer, store=store, preflight=guarded_preflight if existing or bundled else preflight,
        control_clock=control_clock, desk_capability=capability, clock=clock,
        command_current_guard=incumbent_current if existing or bundled else None,
        **({'bundled_source': options['bundled_rgb_source']} if bundled else {}))
    holder['composition'] = composition
    return composition
