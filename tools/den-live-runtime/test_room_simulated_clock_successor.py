"""Public simulation clock successor; real SDK, authored retained native I/O."""
from copy import deepcopy
from dataclasses import replace
import struct
import unittest

from test_room_canonical_adapter import AdapterFixture


class SimulatedClockSuccessorTests(unittest.TestCase):
    def setUp(self):
        self.f=AdapterFixture(self)
        f=self.f;m=f.modules
        self.install_refused=False
        self.expected_uncertainty=0
        def wire_response(index,op,**fields):
            f.calls.append((index,op,deepcopy(fields)))
            packet=m.wire.open_packet(f.f.binding.key,f.f.binding.device_id,1,
                f.f.binding.context_digest,m.wire.Direction.REQUEST,bytes.fromhex(fields['packet_hex']))
            f.packets.append(packet)
            self.assertIs(packet.operation,m.wire.Operation.TIME_EXCHANGE)
            if packet.body[0]==0:
                echo=struct.unpack('<Q',packet.body[1:])[0]
                body=bytes((int(m.wire.Code.OK),0))+struct.pack('<3Q',echo,1300000,1300000)
            else:
                self.assertEqual(struct.unpack('<IQQQI',packet.body[1:]),(2,1300000,2300000,self.expected_uncertainty,0))
                body=bytes((int(m.wire.Code.REJECTED if self.install_refused else m.wire.Code.ADMITTED),0,0))
            response=m.wire.seal_packet(f.f.binding.key,f.f.binding.device_id,1,f.f.binding.context_digest,
                m.wire.Direction.RESPONSE,packet.operation,packet.session,packet.sequence,body)
            f.original=dict(op='exchange',ok=True,evidence_source='simulated',physical_permission=False,
                physical_truth=False,processed_device_us=fields['at_device_us'],reply_hex=response.hex(),
                packet_hex=fields['packet_hex'])
            return deepcopy(f.original)
        f.plant.step.side_effect=wire_response

    def test_explicit_successor_uses_same_cursor_and_preserves_original_horizon(self):
        f=self.f;owner=f.connect();f.f.now=2300000
        before=owner.clock_observation()
        receipt=owner.refresh_simulated_clock()
        after=owner.clock_observation()
        self.assertEqual((before.mapping_revision,after.mapping_revision),(1,2))
        self.assertEqual(after.observed_home_us,2300000)
        self.assertEqual(after.qualified_until_home_us,4000000)
        self.assertEqual(after.uncertainty_us,0)
        self.assertEqual((after.now_home_us,after.now_device_us),(2300000,1300000))
        self.assertEqual([p.sequence for p in f.packets],[42,43])
        self.assertEqual(f.clock().mapping_revision,1)
        self.assertIs(owner.last_clock_successor,receipt)
        self.assertEqual(receipt.previous_mapping_revision,1)
        self.assertEqual(receipt.mapping_revision,2)
        self.assertFalse(receipt.grant_caps_renewed)

    def test_refused_or_uncertain_install_does_not_renew_clock_or_retry(self):
        f=self.f;owner=f.connect();f.f.now=2300000;self.install_refused=True
        with self.assertRaises(ValueError):owner.refresh_simulated_clock()
        self.assertEqual(owner.clock_observation().mapping_revision,1)
        self.assertIsNone(owner.last_clock_successor)
        count=len(f.packets)
        with self.assertRaises(ValueError):owner.refresh_simulated_clock()
        self.assertEqual(len(f.packets),count)

    def test_successor_preserves_nonzero_original_uncertainty(self):
        f=self.f;raw=f.clock;self.expected_uncertainty=1000
        owner=f.connect(clock_observer=lambda:replace(raw(),uncertainty_us=1000))
        f.f.now=2300000;receipt=owner.refresh_simulated_clock()
        self.assertEqual(owner.clock_observation().uncertainty_us,1000)
        self.assertEqual(receipt.uncertainty_us,1000)
        self.assertEqual(receipt.qualified_until_home_us,4000000)

    def test_changed_raw_horizon_still_refuses_after_explicit_successor(self):
        f=self.f;owner=f.connect();f.f.now=2300000;owner.refresh_simulated_clock()
        raw=owner._clock.observer
        owner._clock.observer=lambda:replace(raw(),qualified_until_home_us=9000000)
        with self.assertRaisesRegex(ValueError,'explicit successor'):owner.clock_observation()

    def test_expired_original_clock_cannot_be_resuscitated_by_refresh(self):
        f=self.f;raw=f.clock
        owner=f.connect(clock_observer=lambda:replace(raw(),qualified_until_home_us=10000000))
        f.f.now=6100001
        with self.assertRaisesRegex(ValueError,'current exact simulated'):owner.refresh_simulated_clock()
        self.assertEqual(f.packets,[])


if __name__=='__main__':unittest.main()
