"""Persistent camera/review controller with explicitly requested short trials."""
from copy import deepcopy
import os
from pathlib import Path
import time

from live_detector_app import private_json, validate_config, build_runtime
from managed_pair import POSE_EXPERIMENTS


def create_managed_refresh_app():
    from refresh_app import create_refresh_app
    return create_refresh_app(capture_factory=create_managed_detector_app)


class ReceiptLease:
    def __init__(self, receipts, clock=time.time):
        self.expires_at = min(r['expires_at'] for r in receipts.values())
        self.clock = clock

    def ready(self):
        return self.clock() < self.expires_at


def enabled_recipes(config):
    """Live recipes this provisioning can prepare: placement-same always, the
    pose pair with pose profiles, and the V-JEPA on/off experiment (the pose
    pair plus the temporal_encoder lever) only with an enabled vjepa_live block."""
    enabled = {'placement-same'}
    if 'pose_profiles' in config:
        enabled.add('placement-best')
        if config.get('apartment_viewing') is not None:
            from apartment_viewing_source import policy
            policy(config['apartment_viewing']);enabled.add('apartment-viewing')
        if (config.get('vjepa_live') or {}).get('enabled') is True:
            enabled.add('vjepa-onoff')
    return enabled


def check_reasoning_windows(recipes, enabled, reasoning_factory):
    """Reject, never clamp, an enabled recipe whose history exceeds the JEV bound."""
    check = getattr(reasoning_factory, 'check_windows', None)
    for recipe in recipes:
        if check is None or recipe['id'] not in enabled:
            continue
        try:
            check({arm: recipe[arm]['history'] for arm in ('a', 'b')})
        except ValueError as error:
            raise ValueError(f"Recipe {recipe['id']} cannot run live JEV: {error}") from None


def check_ha_provisioning(settings, source, config, reasoning_factory):
    """Fail closed at startup, before any worker is prepared for a session."""
    bindings = getattr(reasoning_factory, 'ha_bindings', None) or {}
    if bindings and not config.get('ha_source_file'):
        raise ValueError('JEV HA bindings require a provisioned HA source file')
    if config.get('ha_source_file'):
        from managed_sources import ha_source
        ha_source(settings.site_id, source.camera_id, config['ha_source_file'], ha_bindings=bindings)


def check_caption_startup(recipes, enabled, config, reasoning_factory):
    """Fail closed at startup, like HA: an enabled recipe granting captions needs
    the live 'caption_history' opt-in, the household caption egress grant and
    a resolvable caption source (the managed refresh publisher's file).
    Returns the caption provisioning facts the controller advertises."""
    from live_detector_app import PROCESS_STARTED_AT, check_caption_provisioning
    granted = []
    for recipe in recipes:
        if recipe['id'] not in enabled:
            continue
        check_caption_provisioning(recipe, config, reasoning_factory)
        if any(isinstance(recipe.get(arm), dict) and recipe[arm].get('caption_evidence') is True for arm in ('a', 'b')):
            granted.append(recipe['id'])
    source = {'provisioned': False, 'reason': 'caption_history_not_provisioned'}
    if config.get('caption_history') is True:
        from caption_history import refresh_publisher_binding
        try:
            refresh_publisher_binding(PROCESS_STARTED_AT)
            source = {'provisioned': True, 'reason': None}
        except Exception:
            if granted:
                raise ValueError(f'Recipe {granted[0]} grants captions without a provisioned caption source') from None
            source = {'provisioned': False, 'reason': 'caption_source_unavailable'}
    return {'source': source, 'granted_recipes': granted}


def create_managed_runtime(store, source, config, clients, *, reasoning_factory=None, spatial_readiness=None, transport_factory=None, la_live=None):
    import json
    from managed_readiness import ManagedReadiness, ReadinessPair
    from qwen_transport import saved_analysis
    recipes = json.loads(Path(__file__).with_name('experiments.json').read_text())['experiments']
    enabled=enabled_recipes(config)
    check_reasoning_windows(recipes, enabled, reasoning_factory)
    caption = check_caption_startup(recipes, enabled, config, reasoning_factory)
    # Controller-lifetime saved Qwen analysis; absent keys leave it unavailable
    # (qwen_admission_not_provisioned), an invalid pair fails startup.
    analysis = saved_analysis(store, config.get('qwen_profile'), config.get('qwen_transport'), transport_factory=transport_factory)
    apartment_metric=None;metric_status={'present':False,'provisioned':False,'reason':'metric_viewing_not_configured'}
    if config.get('apartment_viewing') is not None:
        from apartment_viewing_metric_bootstrap import load_optional_factory
        from preview_client import LocalBundleClient
        from preview_app import camera_token
        apartment_metric,metric_status=load_optional_factory(client=LocalBundleClient(lambda:camera_token(config['camera_env'])))
        if metric_status['present'] and not metric_status['provisioned']:enabled.discard('apartment-viewing')
    catalog = [{**r, 'status': 'unverified' if r['id'] in enabled else 'blocked',
                'readiness_reason': 'prepares_on_start' if r['id'] in enabled
                                    else 'experiment_pair_not_provisioned'} for r in recipes]

    def factory(receipts,experiment_id='placement-same'):
        if experiment_id=='apartment-viewing' and metric_status['present'] and not metric_status['provisioned']:
            raise ValueError(metric_status['reason'])
        value = deepcopy(config)
        prepared={}
        for arm in ('a', 'b'):
            supplied = receipts[arm]['profile']
            expected = value['pose_profiles'][arm] if experiment_id in POSE_EXPERIMENTS else value['arms'][arm]['profile']
            # Only engine identity changes. A receipt cannot silently change
            # checkpoint, precision, device, preprocessing or runtime build.
            if {k: v for k, v in supplied.items() if k != 'engine_epoch'} != {
                    k: v for k, v in expected.items() if k != 'engine_epoch'}:
                raise ValueError('Prepared worker differs from registered profile')
            prepared[arm] = supplied
        value = validate_config(value)
        return build_runtime(store, source, value, lease=ReceiptLease(receipts),experiment_id=experiment_id,profiles_override=prepared,reasoning_factory=reasoning_factory,
                             la_live=la_live,apartment_metric_source=apartment_metric if experiment_id=='apartment-viewing' else None)

    # Capabilities add a cached launch-free host probe; admission stays the static catalog.
    pair = ReadinessPair(store, clients, factory, catalog, reasoning_available=reasoning_factory is not None,reasoning_readiness=getattr(reasoning_factory,'readiness',None),
                         setup_readiness=ManagedReadiness(clients, enabled), analysis_jobs=analysis,
                         caption_readiness=caption, spatial_readiness=spatial_readiness)
    if config.get('apartment_viewing') is not None:
        from apartment_viewing_source import capability
        pair.apartment_viewing_capability=capability(config['apartment_viewing'])
        if metric_status['present']:pair.apartment_viewing_capability['metric_source']=metric_status
        if metric_status['present'] and not metric_status['provisioned']:
            pair.apartment_viewing_capability.update(provisioned=False,reason=metric_status['reason'])
        if apartment_metric is not None:pair.apartment_viewing_capability.update(apartment_metric.capability)
    from frame_ring import register_controller_runtime_scope
    pair.capture_scope = register_controller_runtime_scope(pair)
    if la_live is not None:
        # Controller lifetime, like saved analysis: started with this runtime
        # and stopped first when it closes (la_live_perceiver.attach).
        from la_live_perceiver import attach
        attach(pair, la_live)
    return pair


def create_managed_detector_app(*, refresh_backend=None, refresh_gateway_subject=None):
    from app import create_app
    from capture_app import load_provisioning
    from live_video import LiveVideo
    from live_planes import LivePlanes
    from preview_client import LocalPreviewClient
    from preview_app import camera_token
    from worker_lifecycle_client import LifecycleClient

    path = os.environ['PERCEPTION_LAB_MANAGED_CONFIG']
    provision = private_json(path)
    if set(provision)-{'jev_config'} != {'schema_version', 'live_config', 'lifecycle'} or provision['schema_version'] != 1:
        raise ValueError('Exact managed provisioning required')
    config = validate_config(private_json(provision['live_config']))
    settings, source, *_ = load_provisioning(config['capture_config'])
    from session_budget import provisioned_budget
    if settings.max_session_seconds != provisioned_budget()['session_seconds']:
        raise ValueError('Recording limit must match commissioned worker stage')
    clients = {}
    if set(provision['lifecycle']) != {'a', 'b'}:
        raise ValueError('Two fixed lifecycle bindings required')
    for arm, binding in provision['lifecycle'].items():
        if arm == 'a':
            if set(binding) != {'kind', 'release'} or binding['kind'] != 'jetson_local':
                raise ValueError('Fixed local Jetson lifecycle required')
            from jetson_lifecycle import JetsonLifecycleClient
            clients[arm] = JetsonLifecycleClient(binding['release'])
            continue
        if set(binding) != {'origin', 'token_file', 'ca_file', 'site_id'}:
            raise ValueError('Exact lifecycle binding required')
        expected_site = 'victoria' if arm == 'a' else 'echo'
        if binding['site_id'] != expected_site:
            raise ValueError('Lifecycle site differs from registered placement')

        def credential(path=binding['token_file']):
            file = Path(path)
            if file.is_symlink() or file.stat().st_uid != os.geteuid() or file.stat().st_mode & 0o077:
                raise ValueError('Private lifecycle credential required')
            token = file.read_text().strip()
            if not 32 <= len(token) <= 512:
                raise ValueError('Invalid lifecycle credential')
            return token

        clients[arm] = LifecycleClient(binding['origin'], credential, binding['ca_file'], expected_site)
    reasoning = None
    if 'jev_config' in provision:
        from managed_jev import reasoning_factory
        reasoning = reasoning_factory(provision['jev_config'])
    check_ha_provisioning(settings, source, config, reasoning)
    if config.get('qwen_live') is not None:
        # Fail closed at startup, like HA: the perceiver feeds live JEV only,
        # and its private transport file must read (no connection is made).
        if reasoning is None:
            raise ValueError('The Qwen live perceiver requires live JEV provisioning')
        from live_detector_app import qwen_live_factory
        qwen_live_factory(config, reasoning_factory=reasoning)
    if (config.get('vjepa_live') or {}).get('enabled') is True:
        # Fail closed at startup: the pump feeds live JEV only, its occupancy
        # gate needs the HA source, its recipe the pose pair, and its private
        # transport file and credentials must read (no connection is made).
        if reasoning is None:
            raise ValueError('The V-JEPA live pump requires live JEV provisioning')
        if 'pose_profiles' not in config or not config.get('ha_source_file'):
            raise ValueError('The V-JEPA live pump requires the pose pair and the HA occupancy source')
        from live_detector_app import vjepa_live_factory
        vjepa_live_factory(config, reasoning_factory=reasoning)
    la_live = None
    if (config.get('la_live_perceiver') or {}).get('enabled') is True:
        # Fail closed at startup, like the other live blocks: its kill switch
        # (la-live-inference-1) is an entry of the household policy, which the
        # JEV provisioning names; its transport file must read (no connection).
        if reasoning is None:
            raise ValueError('The LA live perceiver requires the household policy (live JEV provisioning)')
        # Its occupancy entity must be the HA source's camera-derived one
        # (with the JEV person_present binding), checked before it is built.
        from la_live_perceiver import build
        la_live = build(config, site_id=settings.site_id, camera_id=source.camera_id,
                        policy_file=private_json(provision['jev_config']).get('policy_file'),
                        ha_bindings=getattr(reasoning, 'ha_bindings', None))
    from live_detector_app import check_room_facts_provisioning, check_spatial_provisioning
    spatial = check_spatial_provisioning(settings, source, config)
    # An enabled room_facts block must name a readable, bound and registered
    # den site map (fail closed at startup, like the spatial file).
    check_room_facts_provisioning(settings, source, config)
    app = create_app(settings, runtime_factory=lambda store: create_managed_runtime(store, source, config, clients, reasoning_factory=reasoning,
                                                                                    spatial_readiness=spatial, la_live=la_live),
                      refresh_backend=refresh_backend, refresh_gateway_subject=refresh_gateway_subject,
                      live_videos={source.camera_id: LiveVideo(source, settings.site_id)},
                      live_planes={source.camera_id: LivePlanes(
                          LocalPreviewClient(lambda: camera_token(config['camera_env'])), settings.site_id, source.camera_id)})
    # Read the actual always-on provider independently of optional Den sessions.
    if settings.site_id == 'victoria' and source.camera_id == 'den':
        from structured_camera_routes import install_structured_camera_context
        install_structured_camera_context(app, source=la_live)
    # An explicit protected file runs the always-on, occupancy-gated activity
    # loop (AL-3): it rolls bounded den recordings with live JEV and publishes
    # activity/pose/health/cost events to the lighting brain, never commands.
    loop_file = os.environ.get('PERCEPTION_LAB_ACTIVITY_LOOP_CONFIG')
    if loop_file:
        from activity_loop import check_config
        loop_config = check_config(private_json(loop_file))
        if loop_config['enabled']:
            if reasoning is None:
                raise ValueError('The activity loop requires live JEV provisioning')
            if la_live is None and config.get('occupancy_reader') is None:
                raise ValueError('The activity loop requires the incumbent HA occupancy reader')
            from activity_loop_runtime import build as build_activity_loop, install as install_activity_loop
            from live_detector_app import private_token
            if la_live is not None:
                occupancy = la_live.reader.state
            else:
                from occupancy_reader import from_config
                reader = from_config(config['occupancy_reader'], clock=app.state.store.clock)
                reader.start()
                occupancy = reader.state
            install_activity_loop(app, build_activity_loop(loop_config, controller=app.state.controller,
                                                           occupancy=occupancy, read_token=private_token,
                                                           read_json=private_json))
    # An explicit protected file installs the fixed supervised Den route before
    # the unchanged Lab authorizer. This prepares no native resident at boot.
    den_file = os.environ.get('PERCEPTION_LAB_DEN_SESSION_CONFIG')
    if den_file:
        from live_den_mount import mount_den_session
        if la_live is None:
            raise ValueError('Den session requires the incumbent occupancy reader')
        return mount_den_session(app, den_file, incumbent=app.state.controller.runtime, occupancy=la_live.reader,
                                 lab_grants=settings.token_grants,la_perceiver=la_live,lab_store=app.state.store)
    return app
