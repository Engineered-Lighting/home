"""Actual authenticated mount5; external I/O/qualification stay authored."""
from copy import deepcopy
import hashlib
from pathlib import Path
import json
import re
import time
import tempfile
import traceback
import unittest
from unittest.mock import patch
import uuid

import live_den_mount as mount
from journal import digest
from archive import ArchiveRefusal, Recorder, _json as parse_record
from test_lab_existing_api_mount import ExistingApiMountTests
from test_lab_stereo_mount import LabStereoMountTests
from room_command_factory import FACTORY_REF,SIMULATION_PROFILE_REF,SOURCE_INVENTORY
from room_canonical_provider import WINDOWS_PROVIDER

_DEFERRED_OWNERS=[]


class LabCommandMountTests(unittest.TestCase):
    def fixture(self):
        f=ExistingApiMountTests();self.addCleanup(f.doCleanups);f.setUp()
        root=Path(__file__).resolve().parents[3]
        declaration=dict(schema='home-den-canonical-command-declaration-1',version=1,
            factory_ref=deepcopy(FACTORY_REF),simulation_profile_ref=deepcopy(SIMULATION_PROFILE_REF),
            provider_pins=deepcopy(WINDOWS_PROVIDER.provider_pins),
            source_bundle_digest=f.inputs['runtime']['source_bundle_digest'],
            source_pins={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in SOURCE_INVENTORY},
            recording=dict(native_file=str(f.source.path/'mounted-native.jsonl'),
                canonical_file=str(f.source.path/'mounted-canonical.jsonl'),
                provenance_file=str(f.source.path/'mounted-provenance.json'),
                max_records=8192,max_bytes=32*1024**2,min_free_bytes=1),
            limits=dict(max_runtime_s=10,interval_s=.02,max_commits=256))
        f.provision.update(schema='home-managed-den-mount-5',version=5,command_capture=declaration)
        f.files['bootstrap.json'].update(schema='home-den-lab-bootstrap-4',version=4,
            command_capture_digest=digest(declaration))
        return f

    def mounted(self,f):
        return mount.mount_den_session(f.app,f.paths['bootstrap_file'],incumbent=f.source.f.runtime,
            occupancy=object(),lab_grants=[dict(token_sha256='e'*64)],lab_store=f.source.f.store,
            stereo_qualification_authority=f.authority)

    def test_bootstrap4_authenticates_before_fixed_factory_or_source_realization(self):
        f=self.fixture();mounted=self.mounted(f)
        self.assertIsNone(mounted.supervisor.supervisor)
        self.assertEqual(f.reads,[])
        response=LabStereoMountTests.request(mounted,'POST','/api/home-live-session/v1/start',
            token='authored-wrong-token',command=f.command('start'))
        self.assertEqual(response.status_code,403)
        self.assertEqual(f.reads,[])

    def test_changed_command_declaration_refuses_before_runtime_realization(self):
        f=self.fixture();mounted=self.mounted(f)
        f.provision['command_capture']['limits']['max_commits']=255
        response=LabStereoMountTests.request(mounted,'POST','/api/home-live-session/v1/start',
            token=f.operator_token,command=f.command('start'))
        self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(response.json()['reason'],'command_declaration_digest_changed')
        self.assertIsNone(mounted.supervisor.supervisor)
        self.assertFalse(Path(f.provision['command_capture']['recording']['native_file']).exists())

    def test_authenticated_mount_owns_four_native_members_and_accepted_cut_through_stop(self):
        f=self.fixture()
        # The fixture's independent input clock is January2027. Double only
        # the existing public Node driver clock port to that same authored time.
        # The actual driver, worker, graph, factory and all native owners run.
        import home_den_runtime.supervisor as supervisor_module
        import lab_stereo_mount_source as source_module
        from lab_live_acquisition import LabLiveAcquisition
        from home_den_runtime.lab_stereo_media import LabStereoMedia
        original=supervisor_module.NodeWorkerDriver.__init__
        drain_cycles=[]
        def timed(driver,**kwargs):
            result=original(driver,**kwargs,controlled_time=f.source.f.now)
            ordinary_drain=driver.drain
            def drained(*,deadline_ms,through_cursor=0):
                # Match the existing session tracer: advance the SAME public
                # worker only after the supervisor's durable withdrawal.
                f.source.f.now+=1
                driver.cycle(f.source.f.now)
                drain_cycles.append(dict(at=f.source.f.now,through_cursor=through_cursor))
                return ordinary_drain(deadline_ms=deadline_ms,through_cursor=through_cursor)
            driver.drain=drained
            return result
        original_composition=source_module.stereo_runtime_composition
        def authored_clocks(*args,**kwargs):
            composition=original_composition(*args,**kwargs)
            # Frozen authored UTC and monotonic must progress together. The
            # real mounted composition retains its actual clock defaults.
            composition.monotonic=f.source.composition.monotonic
            return composition
        source_outcomes=[]
        original_step=LabLiveAcquisition.step
        def observed_step(graph):
            outcome=original_step(graph)
            source_outcomes.append(deepcopy(outcome))
            return outcome
        media_spools=[];retention_failures=[]
        original_retain=LabStereoMedia.retain_media
        def observed_retain(media,refs):
            if not any(value is media for value in media_spools):media_spools.append(media)
            try:return original_retain(media,refs)
            except Exception as error:
                # Content-free diagnostics before the anchor masks this error.
                # No raw Store rows, media, paths, tokens or exception arguments.
                message=str(error)
                retention_failures.append(dict(type=type(error).__name__,
                    reason=message if re.fullmatch(r'[a-z][a-z0-9_]{0,159}',message) else None,
                    message_sha256=hashlib.sha256(message.encode('utf8')).hexdigest(),
                    errno=getattr(error,'errno',None),winerror=getattr(error,'winerror',None),
                    stack=[dict(file=Path(row.filename).name,function=row.name,line=row.lineno)
                           for row in traceback.extract_tb(error.__traceback__)][-12:]))
                raise
        token_path=Path(f.provision['runtime']['worker_config']['token_file'])
        with token_path.open('x',encoding='utf8') as stream:stream.write('authored-no-worker-start')
        mounted=self.mounted(f);f.mounted=mounted
        evidence=Path(r'C:\Claude\home\artifacts\den-mounted-command-owner-20261017\gate-runs')/('run-'+uuid.uuid4().hex)
        evidence.mkdir(parents=True)
        snapshots=[];terminal=None;supervisor=None;admitted_source=None;source_commit_pair=None
        def fail_source_errors():
            failures=[row for row in source_outcomes if row.get('status')=='failed']
            self.assertFalse(failures,failures)
        def accepted_source_pair():
            # Read only the recorder's durable immutable prefix. A concurrent
            # later append cannot turn a partial line into accepted evidence.
            recorded=supervisor.recorder.status()
            self.assertLessEqual(recorded['bytes'],f.provision['runtime']['archive_limits']['max_bytes'])
            with Path(f.provision['runtime']['archive_file']).open('rb') as stream:
                raw=stream.read(recorded['bytes'])
            self.assertEqual(len(raw),recorded['bytes']);self.assertTrue(raw.endswith(b'\n'))
            rows=[json.loads(line)['record'] for line in raw.splitlines()]
            prepared={row['data']['commit']['id']:row['data'] for row in rows if row['kind']=='worker_prepared'}
            desk_receipt=admitted_source['desk']['receipt']
            identity=('cursor','id','producer_id','payload_digest','committed_at')
            expected={key:desk_receipt[key] for key in identity}
            for row in rows:
                if row['kind']!='worker_commit_result':continue
                result=row['data'];ack=result['receipt'];pair=prepared.get(ack['id'])
                if ack.get('status')!='accepted' or pair is None:continue
                commit=pair['commit']
                if expected not in [{key:ref[key] for key in identity} for ref in commit['input_receipts']]:continue
                self.assertEqual(commit['stream_epoch'],desk_receipt['stream_epoch'])
                self.assertGreaterEqual(commit['input_cursor'],desk_receipt['cursor'])
                self.assertEqual(pair['prepared']['commit_id'],commit['id'])
                self.assertEqual(pair['prepared']['decision']['input_cut']['input_receipts'],commit['input_receipts'])
                self.assertEqual(ack['input_cursor'],commit['input_cursor'])
                self.assertEqual(ack['commit_digest'],digest(commit))
                self.assertEqual(result['checkpoint'],commit['checkpoint'])
                return dict(commit_id=commit['id'],input_cursor=commit['input_cursor'],
                    commit_digest=ack['commit_digest'],desk_receipt=deepcopy(desk_receipt))
            return None
        try:
            with patch.object(supervisor_module.NodeWorkerDriver,'__init__',timed), \
                    patch.object(source_module,'stereo_runtime_composition',side_effect=authored_clocks), \
                    patch.object(LabLiveAcquisition,'step',observed_step), \
                    patch.object(LabStereoMedia,'retain_media',observed_retain):
                started=f.request('start',token=f.operator_token)
                self.assertEqual(started.status_code,200,started.text)
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    response=f.request('status',token=f.operator_token)
                    self.assertEqual(response.status_code,200,response.text)
                    value=response.json();snapshots.append(value)
                    supervisor=mounted.supervisor.supervisor
                    fail_source_errors()
                    admitted=[row for row in source_outcomes if row.get('status')=='admitted_desk'
                        and row.get('current') is True and row.get('operational_geometry') is True]
                    if supervisor is not None and supervisor.worker is not None and admitted:
                        self.assertEqual(supervisor.worker.readiness()['status'],'ready')
                        admitted_source=deepcopy(admitted[0])
                        self.assertEqual(admitted_source['desk']['status'],'admitted')
                        self.assertEqual(admitted_source['desk']['receipt']['status'],'accepted')
                        self.assertTrue(admitted_source['capture_receipts'])
                        self.assertTrue(all(receipt['status']=='accepted' for receipt in admitted_source['capture_receipts']))
                        # The first real worker cut must follow completed source
                        # admission, never merely native/worker readiness.
                        supervisor.worker.cycle(f.source.f.now)
                        break
                    self.assertNotIn(value['status'],{'incomplete','refused','stopped'},value)
                    time.sleep(.02)
                else:self.fail('fixed mounted source did not admit current desk geometry before the first worker cut')
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    value=f.request('status',token=f.operator_token).json();snapshots.append(value)
                    fail_source_errors()
                    source_commit_pair=accepted_source_pair()
                    if value.get('commands',{}).get('accepted_commits',0)>0 and source_commit_pair is not None:break
                    self.assertNotIn(value['status'],{'incomplete','refused','stopped'},value)
                    time.sleep(.02)
                else:self.fail('fixed command owner saw no accepted worker pair containing the exact admitted desk receipt')
                f.request('stop',token=f.operator_token)
                terminal=f.wait_terminal(20)
                self.assertEqual(terminal['status'],'stopped',terminal)
                self.assertEqual(terminal['commands']['status'],'drained',terminal)
                self.assertFalse(terminal['commands']['pending'])
                members=terminal['command_drain']['native_receipt']['members']
                self.assertEqual(len(members),4)
                self.assertTrue(all(m['close_acknowledged'] and m['recording_status']['complete'] for m in members))
                recordings=f.provision['command_capture']['recording']
                provenance=Path(recordings['provenance_file']).read_bytes()
                self.assertTrue(provenance.endswith(b'\n'))
                self.assertNotIn(b'\r',provenance)
                with Path(recordings['canonical_file']).open('rb') as stream:
                    header=json.loads(stream.readline())['record']
                self.assertEqual(header['kind'],'session_manifest')
                authorities=header['data']['manifest']['extension']['binding']['authorities']
                self.assertEqual(len(authorities),4)
                for authority in authorities:
                    self.assertEqual(authority['context']['source_cut'],hashlib.sha256(provenance).hexdigest())
                self.assertFalse(terminal['physical_authority'])
                self.assertTrue(supervisor.graph.finished)
        finally:
            if supervisor is not None and supervisor.thread is not None and supervisor.thread.is_alive():
                f.request('stop',token=f.operator_token)
                supervisor.thread.join(12)
            active=supervisor is not None and supervisor.thread is not None and supervisor.thread.is_alive()
            if active:
                # Preserve owners/sinks; the reviewed outer Job retains final
                # process cleanup. Never delete files under unconfirmed writers.
                _DEFERRED_OWNERS.append(f);f._cleanups=[]
            selected=dict(f.provision['command_capture']['recording'])
            selected.update(worker_file=f.provision['runtime']['archive_file'],
                lab_originals=f.provision['producer']['original_archive']['filename'])
            hashes={}
            for key in ('native_file','canonical_file','provenance_file','worker_file','lab_originals'):
                path=Path(selected[key])
                if path.exists():
                    raw=path.read_bytes();target=evidence/(key+path.suffix)
                    with target.open('xb') as stream:stream.write(raw)
                    hashes[key]=dict(sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
            # Preserve terminal spool evidence without exporting private bodies.
            graph=getattr(supervisor,'graph',None)
            capability=getattr(graph,'desk_capability',None)
            media=getattr(capability,'media_archive',None)
            if media is not None and not any(value is media for value in media_spools):media_spools.append(media)
            spools=[]
            for media in media_spools:
                if not media.lock.acquire(False):
                    spools.append(dict(in_flight=True));continue
                try:
                    inventory=[]
                    if media.directory.is_dir() and not media.directory.is_symlink():
                        paths=sorted(path for path in media.directory.rglob('*') if path.is_file())
                        self.assertLessEqual(len(paths),2*media.max_objects+1)
                        for path in paths:
                            self.assertFalse(path.is_symlink())
                            raw=path.read_bytes()
                            inventory.append(dict(path=path.relative_to(media.directory).as_posix(),
                                bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
                    spools.append(dict(in_flight=False,created=media.created,failed=media.failed,
                        sealed=media.sealed,cancelled=media.cancel.is_set(),bytes=media.bytes,
                        acknowledged_objects=len(media.records),binding_digest=media.binding_digest,
                        final_receipt=deepcopy(media.final['receipt']) if media.final is not None else None,
                        file_inventory=inventory,raw_bodies_exported=False))
                finally:media.lock.release()
            report=dict(terminal=terminal,status_snapshots=snapshots,archive_hashes=hashes,
                source_outcomes=source_outcomes,authored_wall_and_monotonic_coupled=True,
                admitted_source=admitted_source,accepted_source_commit_pair=source_commit_pair,
                post_withdrawal_public_worker_cycles=drain_cycles,
                binary_retention_failures=retention_failures,terminal_stereo_spools=spools,
                owned_supervisor_active=active,authored_external_inputs=True,authored_Node_clock=True,
                installed_factory_assembly=True,positive_light_proven=False,ordinary_replay=False,
                physical_permission=False,physical_truth=False)
            (evidence/'diagnostics.json').write_text(json.dumps(report,sort_keys=True,indent=2),encoding='utf8')


class RecorderBinaryIoTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='den-recorder-binary-')
        self.addCleanup(self.work.cleanup)
        self.manifest = {'schema': 'home-live-session-manifest-1', 'version': 1}

    def recorder(self, name, *, max_bytes=4096):
        value = Recorder(Path(self.work.name) / name, self.manifest,
            max_records=32, max_bytes=max_bytes, min_free_bytes=1)
        self.addCleanup(lambda: value.drain({'status': 'incomplete'}))
        return value

    def assert_physical_status(self, recorder):
        snapshot = recorder.status()
        raw = recorder.path.read_bytes()
        self.assertEqual(len(raw), snapshot['bytes'])
        self.assertTrue(raw.endswith(b'\n'))
        self.assertNotIn(b'\r\n', raw)
        rows = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual(len(rows), snapshot['records'])
        previous = None
        for seq, row in enumerate(rows):
            self.assertEqual(row['seq'], seq)
            self.assertEqual(row['previous_digest'], previous)
            self.assertEqual(row['digest'], digest({key: value for key, value in row.items() if key != 'digest'}))
            previous = row['digest']
        self.assertEqual(previous, snapshot['last_digest'])
        return snapshot, raw, rows

    def test_actual_bytes_lf_boundary_and_durable_digest_match_status_after_every_append(self):
        recorder = self.recorder('prefix.jsonl')
        self.assert_physical_status(recorder)
        record = {'kind': 'worker_cut', 'data': {'label': 'first\nsecond', 'items': []}}
        receipt = recorder.record(record)
        snapshot, prefix, rows = self.assert_physical_status(recorder)
        self.assertEqual(rows[-1]['record'], record)
        self.assertEqual(rows[-1]['digest'], receipt['digest'])
        recorder.record({'kind': 'worker_tick', 'data': {'t': 1}})
        self.assert_physical_status(recorder)
        # Exercise the mounted gate's physical-offset read against the original
        # durable snapshot, even after a later append has enlarged the file.
        with recorder.path.open('rb') as stream:
            retained = stream.read(snapshot['bytes'])
        self.assertEqual(retained, prefix)
        self.assertTrue(retained.endswith(b'\n'))
        self.assertEqual(json.loads(retained.splitlines()[-1])['digest'], snapshot['last_digest'])
        # Historical CRLF-delimited records retain the same JSON and digests
        # through the unchanged strict parser used by the Python replay reader.
        historical = retained.replace(b'\n', b'\r\n')
        self.assertEqual([parse_record(line) for line in historical.splitlines()],
            [parse_record(line) for line in retained.splitlines()])
        self.assertEqual(parse_record(b'{}\n'), {})
        with self.assertRaises(ValueError):
            parse_record(bytes((123, 125, 92, 110)))  # {} followed by literal backslash+n.
        with self.subTest('exact complete-record physical budget'):
            self.check_exact_physical_byte_budget()
        with self.subTest('one byte below the complete-record boundary'):
            self.check_one_byte_short_budget()

    def measured_record_boundary(self):
        probe = self.recorder('probe-'+uuid.uuid4().hex+'.jsonl')
        header = len(probe.path.read_bytes())
        record = {'kind': 'worker_cut', 'data': {'label': 'x' * 1200}}
        probe.record(record)
        boundary = len(probe.path.read_bytes())
        self.assertGreaterEqual(boundary - 1, 1024)
        self.assertEqual(len(str(boundary)), len(str(probe.max_bytes)),
            'the fixture changes capacity without changing the serialized header width')
        return header, record, boundary

    def check_exact_physical_byte_budget(self):
        header, record, boundary = self.measured_record_boundary()
        recorder = self.recorder('exact.jsonl', max_bytes=boundary)
        self.assertEqual(len(recorder.path.read_bytes()), header)
        recorder.record(record)
        _, raw, _ = self.assert_physical_status(recorder)
        self.assertEqual(len(raw), boundary)
        with self.assertRaisesRegex(ArchiveRefusal, 'archive_budget_exhausted'):
            recorder.record({'kind': 'worker_tick', 'data': {'t': 1}})
        self.assertEqual(recorder.path.read_bytes(), raw)
        self.assertEqual(recorder.status()['bytes'], boundary)

    def check_one_byte_short_budget(self):
        header, record, boundary = self.measured_record_boundary()
        recorder = self.recorder('short.jsonl', max_bytes=boundary - 1)
        before = recorder.path.read_bytes()
        self.assertEqual(len(before), header)
        with self.assertRaisesRegex(ArchiveRefusal, 'archive_budget_exhausted'):
            recorder.record(record)
        self.assertEqual(recorder.path.read_bytes(), before)
        self.assertEqual(recorder.status()['bytes'], header)
        self.assertEqual(recorder.status()['records'], 1)



if __name__=='__main__':unittest.main()
