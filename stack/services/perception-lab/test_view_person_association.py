"""Bounded anonymous image continuity; authored software evidence only."""
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from view_person_association import ViewPersonAssociator


def scope(**changes):
    value = dict(site_id='victoria', camera_id='den', session_id='session', session_generation=0,
                 arm='b', generation=1, capture_epoch='capture', engine_epoch='engine',
                 artifact_sha256='a'*64, model_version='pose-v1')
    value.update(changes)
    return value


def source(index=1, **changes):
    value = dict(frame_id='frame-'+str(index), request_id='request-'+str(index), observed_at=float(index),
                 available_at=index+.1, valid_until=index+1., width=640, height=360)
    value.update(changes)
    return value


def person(identity, x):
    return dict(instance_id=identity, box=dict(x=x, y=50., w=100., h=200.))


class ViewAssociationTests(unittest.TestCase):
    def test_reordered_frame_instances_keep_only_unique_anonymous_continuity(self):
        bank = ViewPersonAssociator()
        first = bank.preview(scope(), source(), [person('first-left', 20.), person('first-right', 400.)])
        bank.commit(first)
        second = bank.preview(scope(), source(2), [person('next-right', 402.), person('next-left', 22.)])
        refs = {row['instance_id']: row['track_ref'] for row in first.association['assignments']}
        self.assertEqual([row['track_ref'] for row in second.association['assignments']],
                         [refs['first-right'], refs['first-left']])
        self.assertEqual([row['status'] for row in second.association['assignments']], ['continued', 'continued'])
        self.assertTrue(second.association['viewing_only'])
        self.assertFalse(second.association['identity_authority'])

    def test_duplicate_cannot_bypass_current_or_clock_retirement(self):
        for flags, reason in (({'current': False}, 'not_current'),
                              ({'clock_verified': False}, 'clock_unverified'),
                              ({'uncertainty_s': 1.}, 'source_expired')):
            with self.subTest(flags=flags):
                bank = ViewPersonAssociator()
                first = bank.preview(scope(), source(), [person('one', 20.)])
                bank.commit(first)
                refused = bank.preview(scope(), source(), [person('one', 20.)], **flags)
                self.assertEqual(refused.association['status'], 'unavailable')
                self.assertEqual(refused.association['reason'], reason)
                self.assertEqual(refused.association['retired'][0]['track_ref'], first.association['assignments'][0]['track_ref'])
                bank.commit(refused)
                next_frame = bank.preview(scope(), source(2), [person('next', 21.)])
                self.assertEqual(next_frame.association['assignments'][0]['status'], 'new')


class RuntimeAssociationCommitTests(unittest.TestCase):
    def test_failed_output_transaction_cannot_advance_anonymous_bank(self):
        from config import Settings
        from store import Store
        from runtime import PairedRunner

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Settings(storage_root=Path(directory), min_free_bytes=0), clock=lambda: 100.)
            session, _ = store.create_session('den', 'detection', 'Authored association commit', 'fixture-request')
            store.transition(session['id'], 'starting');store.transition(session['id'], 'recording')
            profiles = {arm: dict(profile_id=arm, artifact_sha256='a'*64, model_version='fixture', engine_epoch='engine') for arm in ('a', 'b')}
            release = threading.Event();entered = {arm: threading.Event() for arm in ('a', 'b')};requests = {}

            def receipt(request):
                x = 20. if request['frame']['frame_id'] == 'first' else 400.
                result = dict(schema_version=1, frame_id=request['frame']['frame_id'], coordinate_space='source_pixels',
                              boxes=[dict(person('local-'+request['frame']['frame_id'], x)['box'], label='person', instance_id='local-'+request['frame']['frame_id'])])
                return dict(request_id=request['request_id'], engine_epoch='engine', artifact_sha256='a'*64,
                            terminal=True, state='completed', result=result)

            def execute(request):
                if request['frame']['frame_id'] == 'failed':
                    requests[request['arm']] = request;entered[request['arm']].set()
                    if not release.wait(3):
                        raise RuntimeError('authored_worker_timeout')
                return receipt(request)

            runner = PairedRunner(store, session['id'], profiles, execute, clock=lambda: 100., experiment_generation=1)

            def frame(identity, observed):
                media = store.add_media(session['id'], b'authored-pixels', 'image/jpeg', dict(
                    site_id='victoria', camera_id='den', frame_id=identity, capture_epoch='capture', width=640, height=360,
                    observed_at=observed, valid_until=101., clock_mapping_status='verified', clock_mapping_id='authored'))
                return dict(frame_id=identity, capture_epoch='capture', site_id='victoria', camera_id='den', session_id=session['id'],
                            observed_at=observed, available_at=observed+.01, valid_until=101., media_ids=[media['id']])

            try:
                runner.offer(frame('first', 99.))
                limit = time.monotonic()+3
                while any(runner.snapshot()['active'].values()):
                    self.assertLess(time.monotonic(), limit);time.sleep(.002)
                first = next(event['payload']['arm_snapshot']['persons2d']['association'] for event in store.list_events(session['id'])
                             if event['kind'] == 'perception.output' and event['payload']['arm'] == 'b')
                runner.offer(frame('failed', 99.2))
                self.assertTrue(all(event.wait(2) for event in entered.values()))
                original_transaction = store.transaction

                @contextmanager
                def failed_commit():
                    with original_transaction() as db:
                        yield db
                        raise RuntimeError('authored_output_commit_failed')

                with patch.object(store, 'transaction', failed_commit):
                    with self.assertRaisesRegex(RuntimeError, 'authored_output_commit_failed'):
                        runner.reconcile(requests['b']['request_id'], receipt(requests['b']))
                self.assertFalse(any(event['kind'] == 'perception.output' and event['payload']['frame_id'] == 'failed'
                                     for event in store.list_events(session['id'])))
                candidate = runner.view_person_associations['b'].preview(first['scope'], source(3,
                    observed_at=99.4, available_at=100., valid_until=101.), [person('next-left', 22.)])
                self.assertEqual(candidate.association['assignments'][0]['status'], 'continued')
                self.assertEqual(candidate.association['assignments'][0]['track_ref'], first['assignments'][0]['track_ref'])
            finally:
                release.set();runner.close();runner.pool.shutdown(wait=True);store.close()


if __name__ == '__main__':
    unittest.main()
