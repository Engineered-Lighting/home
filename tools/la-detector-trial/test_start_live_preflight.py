from contextlib import redirect_stderr
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import start_live

HOUR = 3600
DAY = 24*HOUR
NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc).timestamp()
OCT_23 = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc).timestamp()
BASELINE = int(datetime(2026, 9, 26, 23, 7, tzinfo=timezone.utc).timestamp())
NO_FAULT = (1, '-- No entries --\n', '')
GENERIC = 'new fault or unavailable fault check'
INCOMPLETE = 'kernel fault scan incomplete'


class Clock:
    """Wall and monotonic time that move only when the fake journal reads."""
    def __init__(self, wall):
        self.wall, self.mono = wall, 5000.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


class Journal:
    """journalctl -k double. Reading costs seconds_per_day of window; a run past
    its timeout raises like subprocess.run does. Each run notes the checkpoint
    on disk when it starts, to prove clean chunks are recorded at once."""
    def __init__(self, clock, base, faults=(), seconds_per_day=0.0, replies=()):
        self.clock, self.base, self.faults = clock, base, list(faults)
        self.seconds_per_day, self.replies, self.calls = seconds_per_day, list(replies), []

    def __call__(self, args, capture_output, text, timeout):
        assert capture_output and text and args[:2] == ['journalctl', '-k']
        assert args[-3:] == ['--no-pager', '--grep', start_live.KERNEL_FAULTS]
        since = int(args[args.index('--since')+1].removeprefix('@'))
        until = int(args[args.index('--until')+1].removeprefix('@')) if '--until' in args else None
        call = dict(since=since, until=until, timeout=timeout, checkpoint=checkpoint(self.base), clean=False,
                    mark=self.clock.wall)
        self.calls.append(call)
        cost = ((until if until is not None else self.clock.wall) - since)/DAY*self.seconds_per_day
        if cost > timeout:
            self.clock.advance(timeout)
            raise subprocess.TimeoutExpired(args, timeout)
        self.clock.advance(cost)
        end = self.clock.wall if until is None else until
        if self.replies:
            code, stdout, stderr = self.replies.pop(0)
        elif any(since <= at <= end for at in self.faults):
            code, stdout, stderr = 0, 'kernel: Out of memory: Killed process 4242 (python3)\n', ''
        else:
            code, stdout, stderr = NO_FAULT
        call['clean'] = (code, stdout, stderr) == NO_FAULT
        call['end'] = end
        return subprocess.CompletedProcess(args, code, stdout, stderr)


def checkpoint(base):
    path = base/'kernel-fault-checkpoint.json'
    return json.loads(path.read_text())['clean_until'] if path.exists() else None


class FailedUnitPreflightTests(unittest.TestCase):
    """Only exact, diagnosed failed units pass; anything new still refuses before later checks."""
    def preflight(self, failed):
        def run(args):
            if args[:2] == ['systemctl', 'list-units']:
                return ''.join(f'{name} loaded failed failed x\n' for name in failed)
            raise AssertionError('reached checks after the failed-unit gate')
        source = {'files': {}}
        with patch.object(start_live.socket, 'gethostname', return_value='EngineeredLightingServer1'), \
             patch.object(start_live, 'run', run), \
             patch.object(start_live.Path, 'read_text', return_value=json.dumps(source)):
            start_live.preflight()

    def test_documented_units_are_exact_names(self):
        self.assertEqual(start_live.DOCUMENTED_FAILED_UNITS, {
            'observer-health.service', 'observer-recover.service',
            'home-agent-preference-offhost-20260928.service', 'home-agent-preference-offhost-20260928-r2.service'})

    def test_documented_failures_reach_the_next_check(self):
        with self.assertRaisesRegex(AssertionError, 'after the failed-unit gate'):
            self.preflight(sorted(start_live.DOCUMENTED_FAILED_UNITS))

    def test_any_other_failed_unit_refuses(self):
        for name in ('home-agent-preference-offhost-20260929.service', 'home-agent-preference-offhost-20260928-r3.service',
                     'hav-vllm.service'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'new failed service'):
                self.preflight(['observer-health.service', name])


class KernelScanTests(unittest.TestCase):
    """The scan reads bounded chunks, records each clean one, and fails closed."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.clock = Clock(NOW)
        for target, name, value in ((start_live, 'BASE', self.base), (start_live, 'time', self.clock)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_checkpoint(self, text):
        (self.base/'kernel-fault-checkpoint.json').write_text(text)

    def scan(self, journal):
        log = io.StringIO()
        with patch.object(start_live.subprocess, 'run', journal), redirect_stderr(log):
            try:
                return start_live.scan_kernel_faults(), log.getvalue()
            except ValueError as error:
                error.log = log.getvalue()
                raise

    def test_recent_checkpoint_reads_one_chunk_to_now(self):
        self.write_checkpoint(json.dumps({'clean_until': NOW-60}))
        journal = Journal(self.clock, self.base, seconds_per_day=6.0)
        clean, log = self.scan(journal)
        self.assertEqual([(c['since'], c['until']) for c in journal.calls], [(int(NOW-65), None)])
        self.assertEqual(clean, NOW)              # the start of the read that reached the end
        self.assertEqual(checkpoint(self.base), NOW)
        self.assertEqual(log, '')

    def test_stale_checkpoint_catches_up_over_leases(self):
        # 2026-11-11: from Oct 23 one scan needed ~110 s at the 50% CPU quota.
        self.write_checkpoint(json.dumps({'clean_until': OCT_23}))
        rate = 110/((NOW-OCT_23)/DAY)
        journal = Journal(self.clock, self.base, seconds_per_day=rate)
        self.assertGreater((NOW-OCT_23)/DAY*rate, start_live.KERNEL_SCAN_SECONDS)
        outcomes = []
        for _ in range(5):
            began = self.clock.monotonic()
            try:
                self.scan(journal)
            except ValueError as error:
                self.assertEqual(str(error), INCOMPLETE)
                self.assertIn('outcome=incomplete', error.log)
                outcomes.append('incomplete')
            else:
                outcomes.append('clean')
                break
            finally:
                self.assertLessEqual(self.clock.monotonic()-began, start_live.KERNEL_SCAN_SECONDS+1e-6)
            self.clock.advance(120)              # the next lease
        self.assertEqual(outcomes, ['incomplete', 'clean'])
        for call in journal.calls:
            self.assertLessEqual(call['timeout'], start_live.KERNEL_SCAN_SECONDS)
            if call['until'] is not None:
                self.assertLessEqual(call['until']-call['since'], start_live.KERNEL_CHUNK_SECONDS)
        # Each clean chunk was on disk before the next read started. A bounded
        # chunk proves up to its --until; the last one up to when it started.
        proven = lambda call: call['mark'] if call['until'] is None else call['until']
        for previous, call in zip(journal.calls, journal.calls[1:]):
            if previous['clean']:
                self.assertEqual(call['checkpoint'], proven(previous))
        # The clean chunks cover the whole window, with no gap.
        covered = int(OCT_23-5)
        for call in journal.calls:
            if call['clean']:
                self.assertLessEqual(call['since'], covered)
                covered = max(covered, proven(call))
        self.assertEqual(checkpoint(self.base), covered)
        self.assertGreaterEqual(covered, NOW)

    def test_fault_in_a_later_chunk_refuses_and_is_never_recorded_as_clean(self):
        start = NOW-2*DAY
        fault = start+13*HOUR
        self.write_checkpoint(json.dumps({'clean_until': start}))
        journal = Journal(self.clock, self.base, faults=[fault], seconds_per_day=1.0)
        for _ in range(3):                           # every later lease still refuses
            with self.assertRaisesRegex(ValueError, '^%s$' % GENERIC) as caught:
                self.scan(journal)
            self.assertIn('outcome=refused', caught.exception.log)
            self.assertLess(checkpoint(self.base), fault)
            self.assertFalse(journal.calls[-1]['clean'])
            self.clock.advance(300)
        self.assertEqual(sum(c['clean'] for c in journal.calls), 2)   # the two chunks before the fault

    def test_fault_in_the_last_chunk_keeps_the_checkpoint(self):
        self.write_checkpoint(json.dumps({'clean_until': NOW-60}))
        journal = Journal(self.clock, self.base, faults=[NOW-30])
        with self.assertRaisesRegex(ValueError, '^%s$' % GENERIC):
            self.scan(journal)
        self.assertEqual(checkpoint(self.base), NOW-60)

    def test_timeout_is_incomplete_and_records_nothing(self):
        self.write_checkpoint(json.dumps({'clean_until': NOW-3*DAY}))
        journal = Journal(self.clock, self.base, seconds_per_day=1000.0)   # one chunk exceeds the budget
        with self.assertRaisesRegex(ValueError, '^%s$' % INCOMPLETE) as caught:
            self.scan(journal)
        self.assertEqual(len(journal.calls), 1)
        self.assertEqual(journal.calls[0]['timeout'], start_live.KERNEL_SCAN_SECONDS)
        self.assertEqual(checkpoint(self.base), NOW-3*DAY)
        self.assertIn('outcome=incomplete', caught.exception.log)
        self.assertIn('clean_until=none', caught.exception.log)

    def test_unproven_replies_are_no_proof_of_a_clean_journal(self):
        for reply in ((0, '', ''), (2, '', ''), (1, '', 'Failed to open journal: Permission denied'),
                      (1, '-- No entries --\n', 'Hint: You are currently not seeing messages from other users'),
                      (1, 'sudo: a password is required\n', ''), (1, 'unexpected\n', '')):
            with self.subTest(reply=reply):
                self.write_checkpoint(json.dumps({'clean_until': NOW-60}))
                journal = Journal(self.clock, self.base, replies=[reply])
                with self.assertRaisesRegex(ValueError, '^%s$' % GENERIC):
                    self.scan(journal)
                self.assertEqual(checkpoint(self.base), NOW-60)

    def test_unproven_reply_after_clean_chunks_keeps_only_those(self):
        self.write_checkpoint(json.dumps({'clean_until': NOW-DAY}))
        journal = Journal(self.clock, self.base, replies=[NO_FAULT, (1, '', 'journal corrupted')])
        with self.assertRaisesRegex(ValueError, '^%s$' % GENERIC):
            self.scan(journal)
        self.assertEqual(checkpoint(self.base), journal.calls[0]['until'])

    def test_unusable_checkpoint_scans_from_the_baseline(self):
        for text in (None, 'not json', '[]', '{}', '{"clean_until": "soon"}', '{"clean_until": null}',
                     '{"clean_until": NaN}', '{"clean_until": Infinity}', '{"clean_until": %r}' % (NOW+HOUR),
                     '{"clean_until": 0}'):
            with self.subTest(text=text):
                path = self.base/'kernel-fault-checkpoint.json'
                if path.exists():
                    path.unlink()
                if text is not None:
                    self.write_checkpoint(text)
                self.assertEqual(start_live.kernel_window_start(NOW), BASELINE)

    def test_usable_checkpoint_starts_five_seconds_early(self):
        for value in (NOW-60, int(NOW-60), NOW):
            with self.subTest(value=value):
                self.write_checkpoint(json.dumps({'clean_until': value}))
                self.assertEqual(start_live.kernel_window_start(NOW), int(value-5))

    def test_clock_behind_the_window_refuses_without_reading(self):
        self.clock.wall = BASELINE-HOUR
        journal = Journal(self.clock, self.base)
        with self.assertRaisesRegex(ValueError, '^%s$' % GENERIC):
            self.scan(journal)
        self.assertEqual((journal.calls, checkpoint(self.base)), ([], None))


class PreflightKernelGateTests(unittest.TestCase):
    """preflight() runs the chunked scan as its last gate."""
    def preflight(self, journal):
        def run(args):
            if args[:2] == ['systemctl', 'list-units']:
                return ''
            if args[:2] == ['docker', 'inspect']:
                return json.dumps([{'State': {'Running': True, 'Health': {'Status': 'healthy'}}}])
            if args[0] == 'nvidia-smi':
                return '20000, 50\n'
            raise AssertionError(args)
        (self.base/'release').mkdir()
        (self.base/'release'/'source.json').write_text(json.dumps({'files': {}}))
        (self.base/'meminfo').write_text('MemTotal: 64000000 kB\nMemAvailable: 32000000 kB\n')
        real = Path
        with patch.object(start_live.socket, 'gethostname', return_value='EngineeredLightingServer1'), \
             patch.object(start_live, 'RELEASE', self.base/'release'), \
             patch.object(start_live, 'run', run), \
             patch.object(start_live, 'Path', lambda p: self.base/'meminfo' if p == '/proc/meminfo' else real(p)), \
             patch.object(start_live.shutil, 'disk_usage', return_value=SimpleNamespace(free=100*1024**3)), \
             patch.object(start_live.subprocess, 'run', journal), redirect_stderr(io.StringIO()):
            return start_live.preflight()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.clock = Clock(NOW)
        for target, name, value in ((start_live, 'BASE', self.base), (start_live, 'time', self.clock)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_clean_journal_passes_and_records(self):
        (self.base/'kernel-fault-checkpoint.json').write_text(json.dumps({'clean_until': NOW-60}))
        self.assertEqual(self.preflight(Journal(self.clock, self.base))[1:], (20000, 50))
        self.assertEqual(checkpoint(self.base), NOW)

    def test_stale_checkpoint_names_the_incomplete_gate(self):
        (self.base/'kernel-fault-checkpoint.json').write_text(json.dumps({'clean_until': OCT_23}))
        journal = Journal(self.clock, self.base, seconds_per_day=6.0)
        with self.assertRaisesRegex(ValueError, '^%s$' % INCOMPLETE):
            self.preflight(journal)
        self.assertGreater(checkpoint(self.base), OCT_23)


if __name__ == '__main__':
    unittest.main()
