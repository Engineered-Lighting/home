"""Opt-in fixed Den composition on the incumbent managed TLS controller.

Only a protected local provisioning file can install this capability. Request
routes select four bounded session operations, never models or Python code.
Construction loads no models, contacts no service and opens no camera.
"""
import importlib.util
from pathlib import Path
import re
import sys

from live_detector_app import private_json, private_token
from live_context_edge import ContextEdge
from live_den_preflight import LocalDenPreflight
from journal import digest
from live_supervised_composition import FileProducerAuthority, ManagedProducerComposition


def _runtime_module():
    location = Path(__file__).resolve().parents[3] / 'tools' / 'den-live-runtime' / '__init__.py'
    name = 'home_den_runtime'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, location, submodule_search_locations=[str(location.parent)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    if Path(sys.modules[name].__file__).resolve() != location:
        raise ValueError('fixed_den_runtime_required')
    # Existing runtime modules use fixed sibling imports. Install only this
    # code-derived directory, never a path named in a request/provision record.
    if str(location.parent) not in sys.path:
        sys.path.append(str(location.parent))
    from home_den_runtime.supervisor import compose_supervisor, SessionControlEdge
    return compose_supervisor, SessionControlEdge


def mount_den_session(app, filename, *, incumbent, occupancy, lab_grants, la_perceiver=None, lab_store=None,
                      stereo_qualification_authority=None, command_factory=None):
    provision = private_json(filename)
    if isinstance(provision, dict) and provision.get('schema') in {
            'home-den-lab-bootstrap-1', 'home-den-lab-bootstrap-2', 'home-den-lab-bootstrap-3', 'home-den-lab-bootstrap-4', 'home-den-lab-bootstrap-5', 'home-den-lab-bootstrap-6', 'home-den-lab-bootstrap-7'}:
        return _mount_bootstrap(app, provision, incumbent=incumbent, occupancy=occupancy,
            lab_grants=lab_grants, la_perceiver=la_perceiver, lab_store=lab_store,
            stereo_qualification_authority=stereo_qualification_authority, command_factory=command_factory)
    return _mount_provision(app, provision, incumbent=incumbent, occupancy=occupancy,
        lab_grants=lab_grants, la_perceiver=la_perceiver, lab_store=lab_store,
        stereo_qualification_authority=stereo_qualification_authority, command_factory=command_factory)


def _mount_bootstrap(app, provision, *, incumbent, occupancy, lab_grants, la_perceiver, lab_store,
                     stereo_qualification_authority=None, command_factory=None):
    from live_den_bootstrap import (DeferredLabSession, DeferredStereoLabSession,
        DeferredExistingApiLabSession, DeferredCommandLabSession, DeferredBundledLabSession,
        DeferredPollBudgetBundledLabSession, DeferredAssessedCommandLabSession)
    _, SessionControlEdge = _runtime_module()
    from home_den_runtime.supervisor import SupervisorRefusal
    if any(g.get('token_sha256') == provision.get('operator_token_sha256') for g in lab_grants):
        raise ValueError('lab_den_credentials_must_be_disjoint')
    assessed_commands = provision['schema'] == 'home-den-lab-bootstrap-7'
    poll_budget = assessed_commands or provision['schema'] == 'home-den-lab-bootstrap-6'
    if assessed_commands and command_factory is not None:
        raise ValueError('fixed_mount8_factory_cannot_be_overridden')
    if poll_budget and not assessed_commands and command_factory is not None:
        raise ValueError('poll_budget_commands_unavailable')
    bundled = poll_budget or provision['schema'] == 'home-den-lab-bootstrap-5'
    commands = provision['schema'] == 'home-den-lab-bootstrap-4'
    existing = bundled or provision['schema'] in {'home-den-lab-bootstrap-3', 'home-den-lab-bootstrap-4'}
    if commands and command_factory is not None:
        raise ValueError('fixed_mount5_factory_cannot_be_overridden')
    if command_factory is not None and (not existing or not callable(command_factory)):
        raise ValueError('installed_mount4_command_factory_required')
    deferred_type = (DeferredAssessedCommandLabSession if assessed_commands else DeferredPollBudgetBundledLabSession if poll_budget else DeferredBundledLabSession if bundled else DeferredCommandLabSession if commands else DeferredExistingApiLabSession if existing else
        DeferredStereoLabSession if provision['schema'] == 'home-den-lab-bootstrap-2' else DeferredLabSession)
    deferred = deferred_type(app, provision=provision,
        loader=lambda frozen: _mount_provision(app, frozen, incumbent=incumbent, occupancy=occupancy,
            lab_grants=lab_grants, la_perceiver=la_perceiver, lab_store=lab_store, publish_state=False,
            stereo_qualification_authority=stereo_qualification_authority, command_factory=command_factory),
        supervisor_refusal=SupervisorRefusal)
    mounted = SessionControlEdge(deferred.dispatch, supervisor=deferred,
        token_sha256=provision['operator_token_sha256'])
    app.state.den_session_supervisor = deferred
    app.state.den_shutdown_guard = deferred.shutdown
    mounted.state = app.state
    return mounted


def _mount_provision(app, provision, *, incumbent, occupancy, lab_grants, la_perceiver=None, lab_store=None,
                     publish_state=True, stereo_qualification_authority=None, command_factory=None):
    keys = {'schema', 'version', 'producer', 'runtime', 'preflight_file', 'context_token_sha256', 'operator_token_sha256'}
    assessed_commands = isinstance(provision, dict) and provision.get('schema') == 'home-managed-den-mount-8'
    poll_budget = assessed_commands or isinstance(provision, dict) and provision.get('schema') == 'home-managed-den-mount-7'
    bundled = poll_budget or isinstance(provision, dict) and provision.get('schema') == 'home-managed-den-mount-6'
    commands = assessed_commands or isinstance(provision, dict) and (provision.get('schema') == 'home-managed-den-mount-5'
        or bundled and provision.get('command_capture') is not None)
    stereo = isinstance(provision, dict) and provision.get('schema') in {'home-managed-den-mount-3', 'home-managed-den-mount-4', 'home-managed-den-mount-5', 'home-managed-den-mount-6', 'home-managed-den-mount-7', 'home-managed-den-mount-8'}
    if (not isinstance(provision, dict) or set(provision) != keys | ({'stereo_source'} if stereo else set()) | ({'command_capture'} if commands or bundled else set())
            or type(provision['version']) is not int or (provision['schema'], provision['version']) not in
                {('home-managed-den-mount-1', 1), ('home-managed-den-mount-2', 2),
                 ('home-managed-den-mount-3', 3), ('home-managed-den-mount-4', 4), ('home-managed-den-mount-5', 5), ('home-managed-den-mount-6', 6), ('home-managed-den-mount-7', 7), ('home-managed-den-mount-8', 8)}
            or any(not isinstance(provision[k], str) or not re.fullmatch(r'[a-f0-9]{64}', provision[k]) for k in ('context_token_sha256', 'operator_token_sha256'))
            or provision['context_token_sha256'] == provision['operator_token_sha256']
            or not isinstance(provision['preflight_file'], str) or not Path(provision['preflight_file']).is_absolute()):
        raise ValueError('closed_den_mount_required')
    p, r = provision['producer'], provision['runtime']
    if assessed_commands and command_factory is not None:
        raise ValueError('fixed_mount8_factory_cannot_be_overridden')
    if assessed_commands and (not isinstance(provision['command_capture'], dict)
            or provision['command_capture'].get('schema') != 'home-den-canonical-command-declaration-3'
            or type(provision['command_capture'].get('version')) is not int
            or provision['command_capture']['version'] != 3):
        raise ValueError('assessed_command_declaration3_required')
    if poll_budget and not assessed_commands and (provision['command_capture'] is not None or command_factory is not None):
        raise ValueError('poll_budget_commands_unavailable')
    expected_runtime = 3 if assessed_commands else 2 if poll_budget else 1
    if (not isinstance(r, dict) or r.get('schema') != 'home-live-runtime-config-' + str(expected_runtime)
            or type(r.get('version')) is not int or r['version'] != expected_runtime
            or not isinstance(r.get('session'), dict)
            or r['session'].get('schema') != 'home-live-session-config-' + str(expected_runtime)
            or type(r['session'].get('version')) is not int or r['session']['version'] != expected_runtime):
        raise ValueError('exact_mount_runtime_session_version_required')
    if not poll_budget and isinstance(p, dict) and (p.get('schema') == 'home-incumbent-lab-producer-7'
                                                   or p.get('version') == 7):
        raise ValueError('fixed_mount7_source7_required')
    if not assessed_commands and isinstance(p, dict) and (p.get('schema') == 'home-incumbent-lab-producer-8'
                                                         or p.get('version') == 8):
        raise ValueError('fixed_mount8_source8_required')
    existing = provision['version'] in (4, 5, 6, 7, 8)
    if commands and command_factory is not None:
        raise ValueError('fixed_mount5_factory_cannot_be_overridden')
    if command_factory is not None and (not existing or not callable(command_factory)):
        raise ValueError('installed_mount4_command_factory_required')
    producer_version = 8 if assessed_commands else 7 if poll_budget else 6
    if bundled and (not isinstance(p, dict) or p.get('schema') != 'home-incumbent-lab-producer-' + str(producer_version)
            or type(p.get('version')) is not int or p['version'] != producer_version):
        raise ValueError('fixed_mount8_source8_required' if assessed_commands else 'fixed_mount7_source7_required' if poll_budget else 'fixed_mount6_source6_required')
    source_version = 5 if assessed_commands else 4
    if poll_budget and (not isinstance(provision['stereo_source'], dict)
            or provision['stereo_source'].get('schema') != 'home-lab-stereo-source-config-' + str(source_version)
            or type(provision['stereo_source'].get('version')) is not int
            or provision['stereo_source']['version'] != source_version):
        raise ValueError('fixed_mount8_source_config5_required' if assessed_commands else 'fixed_mount7_source_config4_required')
    if existing and not bundled and (not isinstance(p, dict) or p.get('schema') != 'home-incumbent-lab-producer-5'
            or type(p.get('version')) is not int or p['version'] != 5):
        raise ValueError('fixed_mount4_source5_required')
    if provision['version'] in (2, 3, 4, 5, 6, 7, 8):
        from lab_live_runtime import lab_graph_configuration, LabRuntimeComposition, compose_lab_supervisor
        store = lab_store if lab_store is not None else getattr(app.state, 'store', None)
        if store is None:
            raise ValueError('incumbent_lab_store_required')
        config = lab_graph_configuration(p, session=store.get_session(p['lab_binding']['session_id']))
    else:
        config = p['acquisition_config']
    live = p['live_config']
    scope = live['manifest']['identity']
    if (r['session']['session_ref'] != scope or config['session_ref'] != scope
            or r['session']['configuration_digest'] != digest(config)
            or not existing and getattr(incumbent, 'capture_scope', None) is None
            or occupancy is None or r.get('assessment_file') is None and config['schema'] in {'home-production-acquisition-4', 'home-production-acquisition-5'}):
        raise ValueError('den_incumbent_scope_or_assessment_required')
    source = live['manifest']['sources'][config['source_id']]
    preflight = LocalDenPreflight(provision['preflight_file'], configuration_digest=digest(config),
        control_policy=source['control_clock'], archive_directory=str(Path(r['archive_file']).parent))
    compose_supervisor, SessionControlEdge = _runtime_module()
    if provision['version'] in (2, 3, 4, 5, 6, 7, 8):
        if stereo:
            from lab_stereo_mount_source import stereo_runtime_composition
            composition = stereo_runtime_composition(p, provision['stereo_source'], store=store,
                preflight=preflight.check, control_clock=preflight.control_clock,
                qualification_authority=stereo_qualification_authority,
                **({'incumbent_runtime': incumbent} if existing else {}))
        else:
            composition = LabRuntimeComposition(p, store=store, preflight=preflight.check,
                control_clock=preflight.control_clock)
        if commands:
            from home_den_runtime.room_command_factory import RoomCommandFactory, validate_command_declaration
            declaration = validate_command_declaration(provision['command_capture'],
                source_bundle_digest=r['source_bundle_digest'])
            if declaration['version'] != (3 if assessed_commands else 2 if bundled else 1):
                raise ValueError('exact_command_source_profile_required')
            if declaration['limits']['max_runtime_s'] > r['session']['max_runtime_s']:
                raise ValueError('command_budget_exceeds_parent_session')
            command_factory = RoomCommandFactory(declaration, composition=composition)
        supervisor = compose_lab_supervisor(r, composition, supervisor_preflight=preflight.supervisor,
            command_factory=command_factory)
    else:
        composition = ManagedProducerComposition(p, controller_scope=incumbent.capture_scope,
            authority=FileProducerAuthority(p['authority_files']), preflight=preflight.check,
            occupancy=occupancy, control_clock=preflight.control_clock,la_perceiver=la_perceiver)
        supervisor = compose_supervisor(r, graph_factory=composition.create, preflight=preflight.supervisor,
            stop_preparation=composition.stop_preparation, release=composition.release)

    def address():
        graph = supervisor.graph
        if graph is None or supervisor.status()['status'] not in {'running', 'stopping'}:
            return None
        return graph.address

    # Context and operator credentials are distinct from each other and Lab.
    # The journal independently validates the context token's narrow grant.
    import hashlib
    publication_file = p['publication_token_file'] if provision['version'] in (2, 3, 4, 5, 6, 7, 8) else p['authority_files']['publication_token']
    publication = hashlib.sha256(private_token(publication_file).encode()).hexdigest()
    if publication in {provision['context_token_sha256'], provision['operator_token_sha256']}:
        raise ValueError('independent_den_credentials_required')
    if any(g.get('token_sha256') in {provision['context_token_sha256'], provision['operator_token_sha256'], publication} for g in lab_grants):
        raise ValueError('lab_den_credentials_must_be_disjoint')
    context_grants = [g for g in p['journal_grants'] if g.get('token_sha256') == provision['context_token_sha256']]
    expected_bindings = [{'manifest_ref': live['manifest_ref'], 'source_id': source_id, 'capabilities': ['read']}
                         for source_id in sorted(live['manifest']['sources'])]
    context_operations = {'read', 'intent'} if provision['version'] == 1 or live['version'] >= 3 else {'read'}
    if (len(context_grants) != 1 or set(context_grants[0].get('operations', ())) != context_operations
            or sorted(context_grants[0].get('live_bindings', ()), key=lambda b: b.get('source_id', '')) != expected_bindings
            or context_grants[0].get('scopes') != [{'site_id': scope['site_id'], 'room_id': scope['room_id']}]
            or any(g.get('token_sha256') == provision['operator_token_sha256'] for g in p['journal_grants'])):
        raise ValueError('narrow_context_journal_grant_required')
    if r['worker_config'] is not None:
        worker_token = private_token(r['worker_config']['token_file'])
        if hashlib.sha256(worker_token.encode()).hexdigest() in {publication, provision['context_token_sha256'], provision['operator_token_sha256'], *[g.get('token_sha256') for g in lab_grants]}:
            raise ValueError('independent_worker_credential_required')

    context = ContextEdge(app, binding={'scope': scope, 'manifest_ref': live['manifest_ref']},
        token_sha256=provision['context_token_sha256'], journal_address=address,intent_version=2 if live["version"]>=6 else 1)
    mounted = SessionControlEdge(context, supervisor=supervisor, token_sha256=provision['operator_token_sha256'])

    def shutdown_guard():
        context.invalidate()
        supervisor.stop(reason='controller_shutdown')
        return composition.release().get('status') == 'released'

    if publish_state:
        app.state.den_session_supervisor = supervisor
        app.state.den_shutdown_guard = shutdown_guard
        # Preserve FastAPI state/lifespan access for the incumbent launcher.
        mounted.state = app.state
    else:
        # A deferred realization must never replace the controller's guard:
        # shutdown can race the still-loading binding before it is published.
        from types import SimpleNamespace
        mounted.state = SimpleNamespace(den_session_supervisor=supervisor, den_shutdown_guard=shutdown_guard)
    return mounted
