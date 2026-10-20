"""Actual Journal/authority/dispatcher/pinned SDK; authored native pipe I/O.

No native process, device, network, model or host executes in this suite. The
coordinator separately runs actual four-member native/room joins. These tests
double only the native step/retained-pipe response, not SDK commands or grants.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/home-context'))
from journal import Journal
from fixture_canonical_dispatch import CanonicalFixtureDispatcher
from fixture_execution_validation import FixtureAuthorityDenied
from test_fixture_canonical_authority import CanonicalAuthorityFixture
from room_canonical_adapter import (RoomCanonicalAdapter, SimulatedClockObservation, load_canonical_modules,
                                    BUILD, MANIFEST_SHA256)
from room_canonical_plant import RoomCanonicalPlant
from room_canonical_plant_recording import RoomCanonicalRecorder
from test_room_canonical_plant import header


class AdapterFixture:
    def __init__(self, case):
        self.directory = tempfile.TemporaryDirectory(prefix='authored-room-adapter-')
        case.addCleanup(self.directory.cleanup)
        self.f = f = CanonicalAuthorityFixture(self.directory.name)
        f.journal.close()
        self.modules = m = load_canonical_modules()
        f.codec = m.plan
        f.binding = m.zha.RuntimeBinding('00:11:22:33:44:55:66:77', '00:11:22:33:44:55:66:80',
            '01' * 16, bytes(range(16)), bytes(range(32)), 1, 2, 4,
            m.plan.encode_context(f.context), 'aa' * 32, 'bb' * 32)
        f.status.update(revision_id='0', state_generation='0')
        f.set_originals()
        f.journal = Journal(Path(self.directory.name) / 'new-same-journal.sqlite')
        case.addCleanup(f.journal.close)
        f.journal._recorder = f.record
        f.authority = f.construct(create=True)
        self.dispatcher = CanonicalFixtureDispatcher(f.journal, authority=f.authority, codec=f.codec,
            clock_home_us=lambda: f.now, create=True)
        f.authority.reservation_reader = self.dispatcher.row
        manifest = json.loads((BUILD / 'manifest.json').read_bytes())
        h = header(manifest); h['fixtures'][0]['fixture_id'] = f.policy.fixture_id
        self.recorder = RoomCanonicalRecorder(Path(self.directory.name) / 'original-pipe.jsonl', header=h)
        case.addCleanup(self.recorder._file.close)
        self.plant = RoomCanonicalPlant(str(BUILD / 'manifest.json'), MANIFEST_SHA256, self.recorder,
            fixture_ids=[r['fixture_id'] for r in h['fixtures']])
        # Authored external process lifecycle. No native member is created.
        self.plant._started = True
        self.original, self.packets, self.calls = None, [], []
        self.foreign_return = False
        def exchange(index, op, **fields):
            self.calls.append((index, op, deepcopy(fields)))
            request = m.wire.open_packet(f.binding.key, f.binding.device_id, 1, f.binding.context_digest,
                m.wire.Direction.REQUEST, bytes.fromhex(fields['packet_hex']))
            self.packets.append(request)
            code = m.wire.Code.ADMITTED if request.operation == m.wire.Operation.PLAN_COMMIT else m.wire.Code.OK
            response = m.wire.seal_packet(f.binding.key, f.binding.device_id, 1, f.binding.context_digest,
                m.wire.Direction.RESPONSE, request.operation, request.session, request.sequence,
                bytes((int(code), 0, 0)))
            self.original = {'op': 'exchange', 'ok': True, 'evidence_source': 'simulated',
                'physical_permission': False, 'physical_truth': False,
                'processed_device_us': fields['at_device_us'], 'reply_hex': response.hex()}
            result = deepcopy(self.original)
            if self.foreign_return: result['reply_hex'] = '00'
            return result
        native = patch.object(self.plant, 'step', side_effect=exchange)
        native.start(); case.addCleanup(native.stop)
        retained = patch.object(self.recorder, 'validated_response', side_effect=lambda _: deepcopy(self.original))
        retained.start(); case.addCleanup(retained.stop)
        self.cursor = m.adapter.SessionCursor(1, 2, f.binding.context_digest.hex(), 41, 0, 0, 0)
        self.owner = None

    def clock(self):
        now = self.f.now; device = 100000 + now - 1100000
        return SimulatedClockObservation(now, device, 1100000, 0, 4000000, 1, device, device)

    def connect(self, **changes):
        args = dict(index=0, dispatcher=self.dispatcher, binding=self.f.binding, session_cursor=self.cursor,
            clock_observer=self.clock, submit_credential='authored-submit-private', observe_credential='authored-observe-private')
        args.update(changes)
        self.owner = RoomCanonicalAdapter(self.plant, **args)
        return self.owner

    def dispatch(self):
        f, d = self.f, self.dispatcher
        identity = 'ambient-1'
        reservation = d.reserve(identity, hashlib.sha256(identity.encode()).hexdigest(), 'autonomous-ambient')
        plan, _ = f.request('autonomous-ambient', identity=identity,
            grant=reservation['grant_id'], revision=reservation['revision_id'])
        admitted = f.authority.admit_ambient(identity, reservation, plan)
        f.now += 100
        d.prepare(identity, admitted)
        return d.dispatch(identity)


class RoomCanonicalAdapterTests(unittest.TestCase):
    def setUp(self): self.f = AdapterFixture(self)

    def test_actual_adapter_dispatches_original_wire_through_owned_room_member(self):
        f = self.f; owner = f.connect()
        result = f.dispatch()
        self.assertEqual(result['record']['adapter_disposition'], 'ADMITTED', result)
        self.assertIs(f.dispatcher.port, owner.port)
        self.assertEqual(f.packets[0].sequence, 42)
        self.assertEqual([p.sequence for p in f.packets], list(range(42, 42 + len(f.packets))))
        self.assertIn(f.modules.wire.Operation.PLAN_PREPARE, [p.operation for p in f.packets])
        self.assertEqual(f.packets[-1].operation, f.modules.wire.Operation.PLAN_COMMIT)
        self.assertTrue(all(index == 0 and op == 'exchange' and set(fields) ==
            {'at_device_us', 'ingress_device_us', 'packet_hex'} for index, op, fields in f.calls))
        self.assertNotIn('authored-submit-private', repr(owner.port))
        self.assertFalse(owner.port.physical_permission)

    def test_fixed_loader_returns_same_verified_types_without_global_c12_aliases(self):
        m = self.f.modules
        before = {k: sys.modules.get(k) for k in ('c12_runtime_plan', 'c12_runtime_wire', 'c12_runtime_adapter')}
        self.assertIs(load_canonical_modules(), m)
        self.assertEqual(before, {k: sys.modules.get(k) for k in before})
        self.assertEqual(m.plan.encode_context(self.f.f.context), self.f.f.binding.context)

    def test_cursor_reset_or_foreign_member_refuses_before_any_exchange(self):
        f = self.f
        for changes in ({'session_cursor': f.modules.adapter.SessionCursor(1, 2, f.f.binding.context_digest.hex(), 0, 0, 0)},
                        {'index': 1}):
            with self.subTest(changes=changes), self.assertRaises(FixtureAuthorityDenied): f.connect(**changes)
        self.assertEqual(f.calls, [])

    def test_one_shot_install_and_read_credential_cannot_submit(self):
        f = self.f; owner = f.connect()
        with self.assertRaises(FixtureAuthorityDenied): f.connect()
        with self.assertRaises(f.modules.adapter.AdapterDenied):
            owner.adapter.submit('authored-observe-private', {'not': 'a command'})
        self.assertEqual(f.calls, [])

    def test_returned_projection_must_equal_original_retained_response(self):
        f = self.f; f.connect(); f.foreign_return = True
        result = f.dispatch()
        self.assertTrue(result['namespace_blocked'], result)
        self.assertNotEqual(result['record']['adapter_disposition'], 'ADMITTED')

    def test_unavailable_status_is_not_replaced_by_core_report(self):
        f = self.f; owner = f.connect()
        with self.assertRaises(f.modules.adapter.AdapterDenied): owner.observe()
        self.assertEqual(f.packets[0].operation, f.modules.wire.Operation.STATUS)
        self.assertEqual(len(f.packets), 1)

    def test_clock_lease_cannot_be_renewed_silently_or_run_backwards(self):
        f = self.f; owner = f.connect()
        f.f.now -= 1
        with self.assertRaises(FixtureAuthorityDenied): owner._clock.sample()
        self.assertEqual(f.calls, [])

    def test_public_observe_refuses_changed_original_clock_uncertainty_before_exchange(self):
        f = self.f; uncertainty = [0]
        owner = f.connect(clock_observer=lambda: replace(f.clock(), uncertainty_us=uncertainty[0]))
        uncertainty[0] = 1
        with self.assertRaisesRegex(FixtureAuthorityDenied, 'clock renewal requires explicit successor'):
            owner.observe()
        self.assertEqual(f.calls, [])


if __name__ == '__main__': unittest.main()
