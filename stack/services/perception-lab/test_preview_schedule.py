"""The preview pump's fixed 2 Hz grid (pump-grid-1) on a virtual clock: no
drift, no bursts, one in-slot retry, capture alignment through the causal
client's hold, and the stored-frame gap distribution against the previous
start-to-start pump under the same heavy-tailed capture preparation."""
import random
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from chrony_preview_client import ChronyPreviewClient
from preview_client import PreviewPump


class VirtualPump:
    """Drives PreviewPump._run on a virtual monotonic clock until `until`."""

    def __init__(self, fetch, *, until, retry_after=None, consume_s=0.03, fail=lambda n: False):
        self.t, self.until, self.starts, self.consumed = 0.0, until, [], []
        self.fetch_impl, self.consume_s, self.fail = fetch, consume_s, fail
        outer = self

        class Fetch:
            def __call__(inner):
                outer.starts.append(outer.t)
                if outer.fail(len(outer.starts)):
                    outer.t += 0.02
                    raise ValueError('Fresh preview temporarily unavailable')
                return outer.fetch_impl(outer, inner)
        self.fetch = Fetch()
        self.pump = PreviewPump(self.fetch, self.consume, lambda: True, retry_after=retry_after, clock=lambda: self.t)
        self.pump._wait_until = self.wait_until

    def consume(self, data, header):
        self.t += self.consume_s
        self.consumed.append(data)

    def wait_until(self, deadline):
        self.t = max(self.t, deadline)
        return self.t > self.until

    def run(self):
        self.pump._run()
        return self


def fixed_work(seconds):
    def fetch(sim, _):
        sim.t += seconds
        return sim.t, 'meta'
    return fetch


class GridScheduleTests(unittest.TestCase):
    def test_polls_hold_the_grid_without_drift(self):
        sim = VirtualPump(fixed_work(.3), until=60).run()
        gaps = [b - a for a, b in zip(sim.starts, sim.starts[1:])]
        self.assertTrue(all(abs(g - .5) < 1e-9 for g in gaps), set(gaps))
        self.assertAlmostEqual(sim.starts[-1], .5 * len(sim.starts), places=6)
        self.assertEqual(sim.pump.counters()['skipped_slots'], 0)

    def test_an_overrun_starts_the_next_slot_late_only_in_its_first_half_and_never_bursts(self):
        work = iter([.65, .1, .1, .1, .9, .1, .1] + [.1] * 200)
        sim = VirtualPump(lambda s, _: (setattr(s, 't', s.t + next(work)) or (s.t, 'meta')), until=10).run()
        # 0.65 s overrun: slot 1.0 starts late at 1.18 (inside its first half);
        # a 0.9 s overrun skips a slot, and the grid phase is kept afterwards.
        self.assertAlmostEqual(sim.starts[1], .5 + .65 + .03)
        self.assertEqual(sim.starts[2:4], [1.5, 2.0])
        self.assertEqual(sim.pump.counters()['skipped_slots'], 1)
        after = [s for s in sim.starts if s > 4]
        self.assertTrue(all(abs((s / .5) - round(s / .5)) < 1e-9 for s in after), after)
        self.assertEqual(len(set(round(s / .5 - .25) for s in sim.starts)), len(sim.starts), 'at most one fetch per slot')

    def test_a_failed_capture_is_retried_once_inside_its_slot(self):
        sim = VirtualPump(fixed_work(.1), until=5, retry_after=.1, fail=lambda n: n in (3, 4, 7)).run()
        counters = sim.pump.counters()
        self.assertEqual(counters['retries'], 2, 'fetch 3 retried as 4 (which failed: no second retry); 7 retried')
        self.assertEqual(sim.starts[2:5], [1.5, 1.62, 2.0])
        self.assertEqual(counters['attempts'], counters['ok'] + 3)
        self.assertEqual(counters['failures'], {'fetch:fresh_preview_temporarily_unavailable': 3})
        with self.assertRaises(ValueError):
            PreviewPump(lambda: None, lambda *a: None, lambda: True, retry_after=.25)

    def test_a_fetch_with_a_capture_lead_starts_early_and_is_told_its_slot(self):
        targets = []

        def fetch(sim, inner):
            targets.append((sim.t, inner.capture_at))
            sim.t += .1
            return sim.t, 'meta'
        sim = VirtualPump(fetch, until=3)
        sim.fetch.capture_lead_s, sim.fetch.capture_at = .25, None
        sim.run()
        self.assertEqual(targets[:3], [(.25, .5), (.75, 1.0), (1.25, 1.5)])
        # A lead beyond half the interval is not trusted: the fetch starts at its slot.
        sim = VirtualPump(fetch, until=2)
        sim.fetch.capture_lead_s, sim.fetch.capture_at = .3, None
        targets.clear()
        sim.run()
        self.assertEqual(targets[0], (.5, None))


def previous_pump(fetch, consume, *, until, clock, advance, interval=.5):
    """The replaced start-to-start pump: sleep what remained of the interval
    after each fetch and ingest; restart at once after an overrun."""
    delay = interval
    while True:
        advance(delay)
        if clock() > until:
            return
        started = clock()
        data, header = fetch()
        delay = max(0, interval - (clock() - started))
        consume(data, header)
        delay = max(0, interval - (clock() - started))


class GapDistributionTests(unittest.TestCase):
    """Heavy-tailed capture preparation (camera health, clock publisher open,
    clock publication) as the recorded receipts show it: usually ~0.08 s, one
    capture in six 0.3-0.8 s. Same seeded sequence for both pumps."""

    def captures(self, pump):
        rng = random.Random(11)
        prep = [rng.uniform(.3, .8) if rng.random() < 1 / 6 else rng.uniform(.05, .12) for _ in range(2000)]
        state = {'t': 0.0, 'i': 0, 'captured': []}

        class CausalClient:
            capture_lead_s = .25
            capture_at = None

            def __call__(self):
                state['t'] += prep[state['i'] % len(prep)]
                state['i'] += 1
                if pump == 'grid' and self.capture_at is not None:
                    state['t'] = max(state['t'], min(self.capture_at, state['t'] + .25))  # the client's hold
                state['captured'].append(state['t'] + .035)
                state['t'] += .1
                return b'jpeg', 'meta'
        client = CausalClient()
        consume = lambda *_: state.__setitem__('t', state['t'] + .03)
        if pump == 'grid':
            runner = PreviewPump(client, consume, lambda: True, clock=lambda: state['t'])

            def wait_until(deadline):
                state['t'] = max(state['t'], deadline)
                return state['t'] > 300
            runner._wait_until = wait_until
            runner._run()
        else:
            previous_pump(client, consume, until=300, clock=lambda: state['t'],
                          advance=lambda d: state.__setitem__('t', state['t'] + d))
        return state['captured']

    def test_the_grid_pump_with_capture_alignment_cuts_long_and_short_gaps(self):
        stats = {}
        for pump in ('previous', 'grid'):
            times = self.captures(pump)
            gaps = [b - a for a, b in zip(times, times[1:])]
            stats[pump] = {'rate_hz': (len(times) - 1) / (times[-1] - times[0]),
                           'over_0_75': sum(g > .75 for g in gaps) / len(gaps),
                           'under_0_25': sum(g < .25 for g in gaps) / len(gaps)}
        previous, grid = stats['previous'], stats['grid']
        self.assertGreater(previous['over_0_75'], .08, stats)
        # This fixture: over 0.75 s 13.3% -> 7.8%, under 0.25 s 12.7% -> 7.8%.
        self.assertLess(grid['over_0_75'], previous['over_0_75'] * .6, stats)
        self.assertLess(grid['under_0_25'], previous['under_0_25'] * .7, stats)
        self.assertLessEqual(grid['rate_hz'], 2.0 + 1e-9, 'never above the 2 Hz steady state')
        self.assertGreater(grid['rate_hz'], previous['rate_hz'] - .01, stats)


class CaptureHoldTests(unittest.TestCase):
    def runtime(self, valid_for_s=2.0):
        config = SimpleNamespace(ntp=SimpleNamespace(server_ip='192.0.2.1', local_clock_quantization_ns=1000,
                                                     drift_slew_ppm=1000, timeout_seconds=.5, valid_for_seconds=1,
                                                     max_uncertainty_seconds=1))
        self.sent = []

        def fetch(path, token):
            self.sent.append((path, time.monotonic()))
            return {'x-victoria-frame-metadata': 'header'}, b'jpeg'

        class Publisher:
            def __init__(self, *_):
                pass

            def publish_once(self):
                return {'valid_until_monotonic_ns': int((time.monotonic() + valid_for_s) * 1e9)}

            def close(self):
                pass

        def accept(value, token, *, transport, publisher_factory, on_phase=lambda phase: None):
            publisher = publisher_factory(value)
            try:
                publisher.publish_once()
                on_phase('jpeg_request')
                transport('/frame.jpg', token)
            finally:
                publisher.close()
            return {'status': 'accepted'}
        pilot = SimpleNamespace(CONFIG='fixed', load_config=lambda _: config, read_token=lambda: 'private-test-token',
                                fetch=fetch, accept_once=accept)
        return pilot, lambda *args: args, Publisher

    def capture(self, client, **kw):
        with patch('chrony_preview_client.commissioned_runtime', return_value=self.runtime(**kw)):
            started = time.monotonic()
            self.assertEqual(client(), (b'jpeg', 'header'))
        return self.sent[-1][1] - started

    def test_the_jpeg_request_waits_for_its_slot_inside_the_receipt(self):
        client = ChronyPreviewClient()
        self.assertEqual(self.capture(client) < .1, True, 'no slot set: the request goes at once')
        self.assertNotIn('capture_hold', client.last_phases_ms)
        client.capture_at = time.monotonic() + .15
        self.assertGreaterEqual(self.capture(client), .13)
        self.assertGreater(client.last_phases_ms['capture_hold'], 100)
        client.capture_at = time.monotonic() + 5
        self.assertLess(self.capture(client), client.MAX_HOLD_S + .1, 'the hold is bounded')
        client.capture_at = time.monotonic() - 1
        self.assertLess(self.capture(client), .1, 'a past slot is never waited for')

    def test_no_hold_that_would_outlive_the_clock_receipt(self):
        client = ChronyPreviewClient()
        client.capture_at = time.monotonic() + .2
        self.assertLess(self.capture(client, valid_for_s=.55), .1, 'receipt valid for 0.55 s: at most 0.05 s of hold')
        client.capture_at = time.monotonic() + .2
        self.assertLess(self.capture(client, valid_for_s=.3), .05)


if __name__ == '__main__':
    unittest.main()
