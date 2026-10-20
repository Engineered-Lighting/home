"""One public timeout/late-cleanup regression using the actual native owner.

Only the installed provider CONSTRUCTION boundary is delayed. Releasing it
constructs the same four pinned native processes; no command-owner state,
recorders, class identities, cleanup or native responses are substituted.
"""
from copy import deepcopy
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest

import test_room_command_session as common


class RoomCommandSessionTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        common.RoomCommandSessionTests.setUpClass()
        cls.native = common.RoomCommandSessionTests.native

    def test_unconfirmed_drain_stays_unconfirmed_after_late_cleanup(self):
        owner = common.source4.LabStereoWorkerRuntimeTests()
        self.addCleanup(owner.doCleanups)
        owner.setUp()
        graph = owner.composition.create()
        owner.graph = owner.source.graph = graph
        helper = common.RoomCommandSessionTests()
        self.addCleanup(helper.doCleanups)
        helper.holder = {}
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        creations = []
        def blocked_provider(*args, **kwargs):
            entered.set()
            if not release.wait(10): raise RuntimeError('authored_provider_construction_deadline')
            native = self.native.provider(*args, **kwargs)
            creations.append(native)
            return native
        helper.native = SimpleNamespace(provider=blocked_provider, manifest=self.native.manifest,
            manifest_path=self.native.manifest_path, scenarios=self.native.scenarios)
        manifest = dict(schema='home-live-session-manifest-1', version=1,
            session_ref=deepcopy(owner.source.scope), configuration_digest=owner.composition.configuration_digest,
            source_bundle_digest='b' * 64, worker_config=deepcopy(owner.worker), worker_description=None)
        recorder = common.Recorder(owner.path / 'timeout-worker-originals.jsonl', manifest,
            max_records=4096, max_bytes=64 * 1024**2, min_free_bytes=1)
        binding = common.RoomCommitBinding(deepcopy(owner.source.scope), owner.worker['worker_id'],
            owner.worker['producer_id'], graph.journal.dynamic.worker.policy['config_digest'],
            graph.journal.dynamic.config['manifest_ref']['digest'], graph.journal.dynamic.worker.worker_version,
            tuple(f['fixture_id'] for f in owner.worker['room_policy']['fixtures']))
        commands = helper._canonical_commands(owner, graph, binding, recorder)
        graph.journal.attach_recorder(commands.record)
        try:
            commands.start()
            self.assertTrue(entered.wait(5), commands.status())
            first = commands.drain(0)
            self.assertEqual(first['status'], 'unconfirmed', first)
            self.assertFalse(first['safe_to_close_journal'])
            self.assertTrue(first['pending'])
            release.set()
            # Observe actual archive finalization, not a guessed delay or a
            # private command-owner event. All four processes really close.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if (helper.holder['sidecar'].status()['status'] == 'drained'
                        and helper.holder['native_recorder'].ready is False): break
                time.sleep(.01)
            else: self.fail('actual late native/canonical cleanup did not settle')
            self.assertEqual(len(creations), 4)
            self.assertTrue(all(p.recording_status['complete'] and not p.recording_status['cleanup_unconfirmed']
                                for p in creations))
            observed = commands.status()
            self.assertEqual(observed['status'], 'unconfirmed', observed)
            self.assertEqual(observed['state'], 'unconfirmed', observed)
            self.assertEqual(commands.readiness()['status'], 'incomplete')
            self.assertEqual(commands.drain(10), first)
        finally:
            release.set()
            commands.quiesce()
            commands.drain(10)
            # Independent teardown of this never-started source graph. There
            # was no worker/source step or issued command; report it incomplete,
            # preserving the frozen timeout rather than claiming a settled run.
            graph.quiesce('authored_timeout_test_cleanup')
            withdrawal = graph.withdraw('authored_timeout_test_cleanup')
            recorder.drain(dict(status='incomplete', reason='authored_timeout_test_cleanup'))
            graph.finalize(reason='authored_timeout_test_cleanup', drain_receipt=dict(
                schema='home-acquisition-drain-1', version=1, session_ref=owner.source.scope,
                configuration_digest=graph.configuration_digest, withdrawal_receipt=withdrawal['barrier_receipt'],
                recorder_status='incomplete', worker_status='unconfirmed',
                terminal_cursor=withdrawal['input_cursor'] or 0))


if __name__ == '__main__': unittest.main()
