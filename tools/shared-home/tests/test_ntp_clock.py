from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import math
import socket
import struct
import threading
import unittest
from unittest.mock import patch

from home_shared import ntp_clock as ntp


BOOT = "12345678-1234-4234-8234-123456789abc"
WALL = 1_800_000_000_000_000_000


def config(**changes):
    # Explicit fixture assumptions only; none are production commissioning.
    value = ntp.NtpClockConfig("127.0.0.1", "fixture-commissioning", ntp.TRUST_ASSUMPTION,
                              .001, 60, 1000, 100, .5, 5, 1)
    return replace(value, **changes)


class Clock:
    def __init__(self, wall=WALL, alter=None):
        self.wall, self.alter, self.calls = wall, alter, 0

    def point(self):
        index = self.calls
        self.calls += 1
        mono = 100_000_000_000 + index * 1_000_000
        point = ntp._ClockPoint(mono, mono + 5_000_001_000,
                               self.wall + index * 1_000_000 + 1000, mono + 2000)
        return self.alter(index, point) if self.alter else point


def stamp(unix_ns):
    seconds, nanos = divmod(unix_ns, 1_000_000_000)
    return struct.pack("!II", (seconds + ntp.NTP_EPOCH_SECONDS) % 2**32,
                       (nanos * 2**32) // 1_000_000_000)


def reply(request, wall=WALL):
    packet = bytearray(48)
    packet[0], packet[1], packet[2], packet[3] = 0x24, 2, 6, 236  # precision -20
    packet[4:8] = struct.pack("!i", 131)  # signed positive ~2ms
    packet[8:12] = struct.pack("!I", 65)
    packet[12:16] = b"TEST"
    packet[16:24] = stamp(wall - 1_000_000_000)
    packet[24:32] = request[40:48]
    packet[32:40] = stamp(wall + 1_200_000)
    packet[40:48] = stamp(wall + 1_400_000)
    return packet


@contextmanager
def udp_fixture(respond):
    """Real UDP only on 127.0.0.1; no production names or hosts are contacted."""
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(1)
    requests, errors = [], []

    def serve():
        try:
            data, peer = server.recvfrom(1024)
            requests.append(data)
            response = respond(data, peer, server)
            if response is not None:
                server.sendto(response, peer)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        with patch.object(ntp, "NTP_PORT", server.getsockname()[1]):
            yield requests
    finally:
        worker.join(timeout=2)
        server.close()
        if worker.is_alive():
            raise AssertionError("fixture thread did not finish")
        if errors:
            raise errors[0]


def sample(*, cfg=None, clock=None, mutate=None, boot_reader=lambda: BOOT):
    clock = clock or Clock()
    def respond(request, _peer, _server):
        packet = reply(request, clock.wall)
        return mutate(packet) if mutate else packet
    with udp_fixture(respond) as requests:
        result = ntp.NtpClockSampler(cfg or config(), _point=clock.point, _boot_id=boot_reader).sample()
    return result, requests


class NtpClockTests(unittest.TestCase):
    def test_local_udp_probe_produces_immutable_exact_camera_evidence(self):
        result, requests = sample()
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(requests[0]), 48)
        self.assertEqual(requests[0][0], 0x23)
        self.assertNotEqual(requests[0][40:48], bytes(8))
        self.assertFalse(result.cryptographically_authenticated)
        self.assertEqual(result.server_trust, ntp.TRUST_ASSUMPTION)
        self.assertEqual(result.poll_exponent, 6)
        value = result.evidence.to_dict()
        self.assertEqual(set(value), {"version", "semantics", "host_boot_id", "validation_id",
                                     "checked_monotonic_ns", "checked_unix_ns", "valid_until_monotonic_ns",
                                     "uncertainty_seconds"})
        self.assertEqual(value["version"], 1)
        self.assertEqual(value["semantics"], "commissioned_camera_host_clock_v1")
        self.assertEqual(value["checked_monotonic_ns"], 100_003_000_000)
        self.assertEqual(value["checked_unix_ns"], WALL + 3_001_000)
        self.assertEqual(value["valid_until_monotonic_ns"], 105_003_000_000)
        self.assertEqual(result.round_trip_seconds, .002002)
        with self.assertRaises(FrozenInstanceError):
            result.evidence.uncertainty_seconds = 0
        value["uncertainty_seconds"] = 0
        self.assertGreater(result.evidence.uncertainty_seconds, 0)

    def test_bound_charges_full_rtt_and_each_explicit_component(self):
        result, _ = sample()
        terms = (result.round_trip_seconds, abs(result.offset_seconds), max(result.root_delay_seconds, 0) / 2,
                 result.root_dispersion_seconds, result.server_accuracy_seconds, result.read_bracket_seconds,
                 result.quantization_seconds, result.drift_slew_allowance_seconds)
        self.assertEqual(result.evidence.uncertainty_seconds, math.nextafter(math.fsum(terms), math.inf))
        self.assertAlmostEqual(result.drift_slew_allowance_seconds, 5.002 * 100 / 1e6)
        self.assertEqual(result.read_bracket_seconds, .00001)
        self.assertEqual(result.precision_seconds, 2**-20)

    def test_signed_negative_root_delay_never_reduces_bound(self):
        def mutate(packet):
            packet[4:8] = struct.pack("!i", -65536)
            return packet
        result, _ = sample(mutate=mutate)
        self.assertEqual(result.root_delay_seconds, -1)
        self.assertGreater(result.evidence.uncertainty_seconds, result.round_trip_seconds + abs(result.offset_seconds))

    def test_rejects_wrong_origin_versions_modes_leap_and_stratum(self):
        def change(index, value):
            def mutate(packet):
                packet[index] = value
                return packet
            return mutate
        for index, value in ((0, 0x1c), (0, 0x23), (0, 0xe4), (0, 0x64), (0, 0xa4),
                             (1, 16), (1, 255), (24, 255)):
            with self.subTest(index=index, value=value):
                if index == 24:
                    def mutation(packet):
                        packet[24] ^= 1
                        return packet
                else:
                    mutation = change(index, value)
                with self.assertRaises(ValueError):
                    sample(mutate=mutation)

    def test_kiss_of_death_is_explicit_and_not_retried(self):
        def mutate(packet):
            packet[1], packet[12:16] = 0, b"RATE"
            return packet
        with self.assertRaises(ntp.NtpKissOfDeath) as error:
            sample(mutate=mutate)
        self.assertEqual(error.exception.code, "RATE")

    def test_zero_reversed_future_reference_and_old_timestamps_fail(self):
        mutations = [(16, bytes(8)), (32, bytes(8)), (40, bytes(8)),
                     (40, stamp(WALL)), (16, stamp(WALL + 2_000_000)),
                     (16, stamp(WALL - 61_000_000_000)),
                     (40, stamp(WALL + 500_000_000))]
        for position, value in mutations:
            def mutate(packet):
                packet[position:position + 8] = value
                return packet
            with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                sample(mutate=mutate)

    def test_exact_unextended_packet_size_is_required(self):
        for size in (0, 47, 49, 1000):
            with self.subTest(size=size), self.assertRaises((ValueError, OSError)):
                sample(mutate=lambda packet: bytes(packet[:size]).ljust(size, b"\x00"))

    def test_large_unsigned_dispersion_precision_offset_and_drift_fail_budget(self):
        def dispersion(packet):
            packet[8:12] = struct.pack("!I", 0xffffffff)
            return packet
        def precision(packet):
            packet[3] = 127
            return packet
        def offset(packet):
            packet[32:40] = stamp(WALL + 2_001_200_000)
            packet[40:48] = stamp(WALL + 2_001_400_000)
            return packet
        for mutate in (dispersion, precision, offset):
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                sample(mutate=mutate)
        # Explicit phase slew allowance, not a hidden 500ppm default.
        with self.assertRaises(ValueError):
            sample(cfg=config(drift_slew_ppm=250_000))
        with self.assertRaises(ValueError):
            sample(cfg=config(max_uncertainty_seconds=.000001))

    def test_timeout_sends_once_and_wrong_udp_peer_cannot_supply_evidence(self):
        for spoof in (False, True):
            def respond(request, peer, _server):
                if spoof:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
                        stranger.bind(("127.0.0.1", 0))
                        stranger.sendto(reply(request), peer)
                return None
            with self.subTest(spoof=spoof), udp_fixture(respond) as requests:
                sampler = ntp.NtpClockSampler(config(timeout_seconds=.04), _point=Clock().point,
                                              _boot_id=lambda: BOOT)
                with self.assertRaises(TimeoutError):
                    sampler.sample()
                self.assertEqual(len(requests), 1)

    def test_absolute_deadline_is_not_renewed_on_receive_or_parse(self):
        for index in (3, 4):
            def alter(which, point):
                if which >= index:
                    return replace(point, before_ns=point.before_ns + 600_000_000,
                                   after_ns=point.after_ns + 600_000_000, boot_ns=point.boot_ns + 600_000_000,
                                   unix_ns=point.unix_ns + 600_000_000)
                return point
            with self.subTest(index=index), self.assertRaises(TimeoutError):
                sample(clock=Clock(alter=alter))

    def test_suspend_wall_step_and_clock_regression_fail_through_completion(self):
        for index in (2, 3, 4):
            for kind in ("suspend", "wall", "regression"):
                def alter(which, point):
                    if which >= index:
                        if kind == "suspend":
                            return replace(point, boot_ns=point.boot_ns + 1_000_000_000)
                        if kind == "wall":
                            return replace(point, unix_ns=point.unix_ns + 1_000_000_000)
                        return replace(point, before_ns=point.before_ns - 5_000_000,
                                       after_ns=point.after_ns - 5_000_000)
                    return point
                with self.subTest(index=index, kind=kind), self.assertRaises(ValueError):
                    sample(clock=Clock(alter=alter))

    def test_boot_change_and_expiry_during_validation_reject(self):
        boots = iter((BOOT, "22345678-1234-4234-8234-123456789abc"))
        with self.assertRaises(ValueError):
            sample(boot_reader=lambda: next(boots))
        with self.assertRaises(TimeoutError):
            sample(cfg=config(valid_for_seconds=.0001))

    def test_era_wrap_and_post_wrap_date_preserve_integer_differences(self):
        wrap = (2**32 - ntp.NTP_EPOCH_SECONDS) * 1_000_000_000
        for wall in (wrap - 1_300_000, wrap + 2_000_000_000, wrap + (2**31) * 1_000_000_000):
            with self.subTest(wall=wall):
                result, _ = sample(clock=Clock(wall=wall))
                self.assertLess(abs(result.offset_seconds), .001)
                self.assertEqual(result.evidence.checked_unix_ns, wall + 3_001_000)
        pivot = WALL
        with self.assertRaises(ValueError):
            ntp._ntp_unix_ns(stamp(pivot + 2**31 * 1_000_000_000), pivot)

    def test_one_outstanding_probe_and_per_call_nonce(self):
        arrived, release = threading.Event(), threading.Event()
        def respond(request, _peer, _server):
            arrived.set()
            if not release.wait(.3):
                raise AssertionError("test did not release response")
            return reply(request)
        sampler = ntp.NtpClockSampler(config(), _point=Clock().point, _boot_id=lambda: BOOT)
        result, failure = [], []
        def run():
            try:
                result.append(sampler.sample())
            except BaseException as exc:
                failure.append(exc)
        with udp_fixture(respond) as requests:
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(arrived.wait(.3))
            with self.assertRaises(RuntimeError):
                sampler.sample()
            release.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(requests), 1)
        self.assertEqual(failure, [])
        self.assertEqual(len(result), 1)
        other, requests2 = sample()
        self.assertNotEqual(requests[0][40:48], requests2[0][40:48])
        self.assertNotEqual(result[0].evidence.validation_id, other.evidence.validation_id)

    def test_old_origin_response_cannot_be_replayed_to_fresh_probe(self):
        _, old = sample()
        old_packet = reply(old[0])
        with self.assertRaises(ValueError):
            sample(mutate=lambda _: old_packet)

    def test_all_commissioning_values_are_required_and_bounded(self):
        with self.assertRaises(TypeError):
            ntp.NtpClockConfig("127.0.0.1")
        for field in ("server_accuracy_seconds", "max_reference_age_seconds", "drift_slew_ppm",
                      "timeout_seconds", "valid_for_seconds", "max_uncertainty_seconds"):
            for value in (-1, float("nan"), float("inf"), True, 10**400):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    config(**{field: value})
        for changes in ({"server_ip": "pool.example.com"}, {"server_ip": "224.0.0.1"},
                        {"server_ip": "0.0.0.0"}, {"server_ip": "::ffff:127.0.0.1"},
                        {"server_trust": "authenticated"}, {"valid_for_seconds": 5.1},
                        {"local_clock_quantization_ns": 0}, {"local_clock_quantization_ns": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                config(**changes)


if __name__ == "__main__":
    unittest.main()
