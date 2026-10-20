"""Fixed on-Start resident preparation inside the incumbent controller.

Only existing UltralyticsWorker and RTMPoseWorker adapters are supported.
Construction is inert. Explicit start verifies local artifacts, independent
preparation authority and current preflight before loading. Missing installed
dependencies are a refusal; no install/download or second camera is attempted.
"""
from copy import deepcopy
from pathlib import Path
import threading
import time

from controller_load import _rss_bytes
from journal import digest, finite
from reasoning import digest as model_digest

PROVISION_KEYS = {'schema', 'version', 'acquisition_config', 'live_config', 'capture_policy', 'capture_method',
                  'spatial_policy', 'interaction_policy', 'assessment_policy', 'journal_path', 'journal_grants',
                  'model_manifests', 'checkpoint_paths', 'authority_files', 'preparation_timeout_s'}
FILE_KEYS = {'local_grant', 'preparation_grant', 'household_enabled', 'publication_token'}


class PreparationUnavailable(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class FileProducerAuthority:
    def __init__(self, files, *, json_reader=None, token_reader=None):
        if not isinstance(files, dict) or set(files) != FILE_KEYS or any(not isinstance(v, str) or not Path(v).is_absolute() for v in files.values()):
            raise ValueError('exact absolute private authority files required')
        # Fixed file adapters are injectable only by trusted test composition,
        # never by a request, observed data or configuration module name.
        from live_detector_app import private_json, private_token
        self.files = dict(files)
        self.json_reader, self.token_reader = json_reader or private_json, token_reader or private_token
        self.local_generation = self.preparation_generation = None
        self.camera_id = None

    def grant(self):
        return self.json_reader(self.files['local_grant'])

    def enabled(self):
        return self.json_reader(self.files['household_enabled']) is True

    def token(self):
        return self.token_reader(self.files['publication_token'])

    def model_allowed(self, kind, phase, manifest, config, now):
        try:
            ref = {'id': manifest['profile_id'], 'digest': model_digest(manifest)}
            local = self.grant()
            from live_acquisition import GRANT_KEYS
            operations = {'capture', 'detector', 'pose', 'spatial'}
            if config.get('interaction_method_ref') is not None:
                operations.add('interaction')
            if config['schema'] in {'home-production-acquisition-4', 'home-production-acquisition-5'}:
                operations.add('assessment')
            if config['schema'] == 'home-production-acquisition-5':
                operations.add('lifecycle')
            if (not isinstance(local, dict) or set(local) != GRANT_KEYS
                    or local['schema'] != 'home-local-acquisition-grant-1' or type(local['version']) is not int or local['version'] != 1
                    or local['id'] != config['local_grant_id'] or local['camera_id'] != self.camera_id
                    or type(local['generation']) is not int or local['generation'] < 1
                    or not all(finite(local[k]) for k in ('not_before', 'valid_until'))
                    or not self.enabled() or config['model_refs'][kind] != ref
                    or local['session_ref'] != config['session_ref'] or local['source_id'] != config['source_id']
                    or local['configuration_digest'] != digest(config) or local['model_refs'] != config['model_refs']
                    or not isinstance(local['operations'], list) or len(local['operations']) != len(operations)
                    or set(local['operations']) != operations or not local['not_before'] <= now < local['valid_until']):
                return False
            if self.local_generation is None:
                self.local_generation = local['generation']
            if local['generation'] != self.local_generation:
                return False
            if phase != 'load':
                return phase in {'infer', 'infer_with_warmup', 'first_infer'}
            grant = self.json_reader(self.files['preparation_grant'])
            valid = (isinstance(grant, dict) and set(grant) == {'schema', 'version', 'session_ref', 'source_id',
                    'configuration_digest', 'model_refs', 'operations', 'not_before', 'valid_until', 'generation'}
                    and grant['schema'] == 'home-resident-preparation-grant-1' and type(grant['version']) is int
                    and grant['version'] == 1 and grant['session_ref'] == config['session_ref']
                    and grant['source_id'] == config['source_id'] and grant['configuration_digest'] == digest(config)
                    and grant['model_refs'] == config['model_refs'] and grant['operations'] == ['resident_preparation']
                    and type(grant['generation']) is int and grant['generation'] > 0
                    and finite(grant['not_before']) and finite(grant['valid_until'])
                    and grant['not_before'] <= now < grant['valid_until'])
            if valid and self.preparation_generation is None:
                self.preparation_generation = grant['generation']
            return valid and grant['generation'] == self.preparation_generation
        except Exception:
            return False


class ManagedProducerComposition:
    def __init__(self, provision, *, controller_scope, authority, preflight, occupancy, control_clock,
                 clock=time.time, monotonic=time.monotonic, rss=_rss_bytes, external_io=None, la_perceiver=None):
        lifecycle = isinstance(provision, dict) and provision.get('acquisition_config', {}).get('schema') == 'home-production-acquisition-5'
        explicit_la = isinstance(provision, dict) and provision.get('schema') == 'home-supervised-den-producer-2' and provision.get('version') == 2
        if (not isinstance(provision, dict) or set(provision) != PROVISION_KEYS | ({'lifecycle_policy'} if lifecycle else set()) | ({'la_binding'} if explicit_la else set())
                or type(provision['version']) is not int
                or not (explicit_la or provision['schema'] == 'home-supervised-den-producer-1' and provision['version'] == 1)
                or set(provision['model_manifests']) != {'detector', 'pose'}
                or set(provision['checkpoint_paths']) != {'detector', 'pose'}
                or any(not isinstance(v, str) or not Path(v).is_absolute() for v in provision['checkpoint_paths'].values())
                or not isinstance(provision['journal_path'], str) or not Path(provision['journal_path']).is_absolute()
                or not finite(provision['preparation_timeout_s']) or not 0 < provision['preparation_timeout_s'] <= 30
                or not isinstance(authority, FileProducerAuthority)
                or not all(callable(v) for v in (preflight, control_clock, clock, monotonic, rss))
                or not callable(getattr(controller_scope, 'require_turns', None))):
            raise ValueError('closed supervised producer provisioning and fixed incumbent capabilities required')
        if explicit_la and provision['la_binding'] is not None:
            binding=provision['la_binding']
            if (not isinstance(binding,dict) or set(binding)!={'schema','version','model_ref','artifact_sha256'}
                    or binding['schema']!='home-live-existing-la-binding-1' or type(binding['version']) is not int or binding['version']!=1):
                raise ValueError('explicit separate LA configuration and artifact pins required')
            from live_ingress_validation import _ref, _sha
            _ref(binding['model_ref']);_sha(binding['artifact_sha256'])
        self.provision = deepcopy(provision)
        self.pin = digest(provision)
        self.scope, self.authority, self.preflight, self.occupancy = controller_scope, authority, preflight, occupancy
        if authority.files != provision['authority_files'] or authority.camera_id is not None:
            raise ValueError('exact unbound private authority capability required')
        authority.camera_id = provision['capture_policy']['camera_id']
        self.control_clock, self.clock, self.monotonic, self.rss = control_clock, clock, monotonic, rss
        self.la_perceiver=la_perceiver
        self.interaction_sources=None
        self.optional_la_status="unavailable"
        self.external_io = dict(external_io or {})
        if set(self.external_io) - {'detector_loader', 'pose_loader', 'camera_dependencies'}:
            raise ValueError('only fixed external test I/O may be replaced')
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.task = self.graph = None
        self.workers = None

    def _admitted(self):
        if self.cancel.is_set() or digest(self.provision) != self.pin:
            return False
        self.scope.require_turns()
        memory = self.rss()
        return (self.preflight() is True and finite(memory) and 0 < memory
                <= self.provision['acquisition_config']['limits']['memory_limit_mb'] * 1024**2)

    def _existing_la_sources(self):
        """Bind only the incumbent already-admitted perceiver; never acquire one."""
        if self.la_perceiver is None:
            return None
        from la_live_perceiver import LaLivePerceiver
        from live_interaction_sources import ExistingReceiptSources
        la=self.la_perceiver
        if not isinstance(la,LaLivePerceiver):
            raise PreparationUnavailable('incumbent_la_capability_required')
        policy=self.provision['interaction_policy']
        artifact=la.pump.base_profile['artifact_sha256']
        binding=self.provision.get('la_binding')
        if (binding is None or policy is None or binding['model_ref'] not in policy['semantic_model_refs']
                or binding['artifact_sha256'] != artifact):
            self.optional_la_status='unavailable_model_pin'
            return None
        def identity():
            profile=la.pump.profile or {}
            return {'engine_epoch':profile.get('engine_epoch'),'artifact_sha256':profile.get('artifact_sha256'),
                'session_id':la.pump.session['id']}
        sources=ExistingReceiptSources(site_id=la.pump.session['site_id'],camera_id=la.pump.session['camera_id'],
            model_ref=binding['model_ref'],artifact_sha256=artifact,granted=la.enabled,identity=identity,clock=self.clock)
        sources.attach(la.fact_log)
        sources.bind_perceiver(la)
        self.optional_la_status='bound_existing_perceiver'
        return sources

    def create(self):
        from live_acquisition import create_production_acquisition, _configuration
        from live_assessment import AssessmentRecorder
        from ultralytics_worker import UltralyticsWorker
        from rtmpose_worker import RTMPoseWorker
        if not self.lock.acquire(False):
            raise PreparationUnavailable('resident_preparation_busy')
        prepared_sources, graph_created = None, False
        try:
            if self.task is not None and not self.task['done'].is_set() or self.graph is not None and self.graph.readiness()['state'] != 'finalized':
                raise PreparationUnavailable('resident_preparation_busy')
            if self.cancel.is_set():
                raise PreparationUnavailable('resident_preparation_cancelled')
            if not self._admitted():
                raise PreparationUnavailable('resident_models_not_provisioned')
            config = self.provision['acquisition_config']
            manifests = self.provision['model_manifests']
            if self.interaction_sources is not None:
                self.interaction_sources.clear()
            self.interaction_sources=prepared_sources=self._existing_la_sources()
            recorder = AssessmentRecorder(self.provision['assessment_policy']) if config['schema'] in {'home-production-acquisition-4', 'home-production-acquisition-5'} else None
            _configuration(config, self.provision['live_config'], self.provision['capture_policy'], self.provision['capture_method'], {},
                           self.provision['spatial_policy'], self.provision['interaction_policy'], self.interaction_sources, recorder,
                           self.provision.get('lifecycle_policy'))
            for kind in ('detector', 'pose'):
                if config['model_refs'][kind] != {'id': manifests[kind]['profile_id'], 'digest': model_digest(manifests[kind])}:
                    raise PreparationUnavailable('resident_model_pin_mismatch')
            def authorize(kind):
                def check(phase, manifest, request_id):
                    return self._admitted() and self.authority.model_allowed(kind, phase, manifest, config, self.clock())
                return check
            detector = UltralyticsWorker(manifests['detector'], authorize('detector'),
                **({'loader': self.external_io['detector_loader']} if 'detector_loader' in self.external_io else {}))
            pose = RTMPoseWorker(manifests['pose'], authorize('pose'),
                **({'loader': self.external_io['pose_loader']} if 'pose_loader' in self.external_io else {}))
            task = {'done': threading.Event(), 'error': False}
            self.task = task
            def prepare():
                try:
                    detector.load(self.provision['checkpoint_paths']['detector'])
                    pose.load(self.provision['checkpoint_paths']['pose'])
                except Exception:
                    task['error'] = True
                finally:
                    task['done'].set()
            threading.Thread(target=prepare, name='home-admitted-resident-prepare', daemon=True).start()
            if not task['done'].wait(self.provision['preparation_timeout_s']):
                self.cancel.set()
                raise PreparationUnavailable('resident_preparation_unconfirmed')
            if task['error'] or not self._admitted():
                self.cancel.set()
                raise PreparationUnavailable('resident_models_not_provisioned')
            # The real path uses the registered controller scope. Only authored
            # external-I/O tests may inject the previously supported test ports.
            self.graph = create_production_acquisition(config, live_config=self.provision['live_config'],
                capture_policy=self.provision['capture_policy'], capture_method=self.provision['capture_method'],
                spatial_policy=self.provision['spatial_policy'], interaction_policy=self.provision['interaction_policy'],interaction_sources=self.interaction_sources,
                assessment_recorder=recorder, shutdown_owner='supervisor', lifecycle_policy=self.provision.get('lifecycle_policy'),
                journal_path=self.provision['journal_path'], journal_grants=self.provision['journal_grants'],
                publication_token=self.authority.token(), detector=detector, pose=pose,
                local_authority=self.authority.grant, household_enabled=self.authority.enabled, occupancy=self.occupancy,
                control_clock=self.control_clock, clock=self.clock, monotonic=self.monotonic, rss=self.rss,
                camera_dependencies=self.external_io.get('camera_dependencies', {'turns': self.scope.require_turns()}))
            self.workers = (detector, pose)
            graph_created = True
            return self.graph
        except PreparationUnavailable:
            raise
        except Exception:
            self.cancel.set()
            raise PreparationUnavailable('resident_models_not_provisioned') from None
        finally:
            if not graph_created and prepared_sources is not None:
                prepared_sources.clear()
                if self.interaction_sources is prepared_sources:
                    self.interaction_sources=None
            self.lock.release()

    def stop_preparation(self):
        self.cancel.set()
        busy = self.task is not None and not self.task['done'].is_set()
        return {'status': 'unconfirmed' if busy else 'quiescent', 'inflight': busy}

    def release(self):
        if self.graph is not None and self.graph.readiness()['state'] != 'finalized':
            return {'status': 'unconfirmed', 'reason': 'producer_drain_required'}
        if self.stop_preparation()['inflight']:
            return {'status': 'unconfirmed', 'reason': 'resident_preparation_busy'}
        for worker in self.workers or ():
            with worker.lock:
                worker.model = None
                worker.warmed = False
        self.workers = None
        if self.interaction_sources is not None:
            self.interaction_sources.clear()
            self.interaction_sources=None
        return {'status': 'released'}
