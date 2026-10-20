"""Local subprocess boundaries only; no SSH or home contact."""
import importlib.util
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location('capture_deployed_source', Path(__file__).parents[1] / 'capture_deployed_source.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class BoundedCaptureTransportTests(unittest.TestCase):
    def command(self, script):
        return [sys.executable, '-I', '-S', '-c', script]

    def test_exact_input_output_and_exit_status(self):
        result = driver.run_bounded(self.command('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.exit(3)'),
                                    input_bytes=b'bounded\0source', output_limit=100, timeout=5)
        self.assertEqual(result.stdout, b'bounded\0source')
        self.assertEqual(result.returncode, 3)

    def test_oversized_stdout_rejected_while_reading(self):
        with self.assertRaises(ValueError):
            driver.run_bounded(self.command('import sys; sys.stdout.write("x"*1000000)'), output_limit=100, timeout=5)

    def test_stderr_has_its_own_bound(self):
        with self.assertRaises(ValueError):
            driver.run_bounded(self.command('import sys; sys.stderr.write("x"*1000000)'), output_limit=100, timeout=5)

    def test_timeout_terminates_specific_child(self):
        with self.assertRaisesRegex(ValueError, 'deadline'):
            driver.run_bounded(self.command('import time; time.sleep(30)'), output_limit=100, timeout=.1)

    def test_oversized_input_never_launches(self):
        with self.assertRaisesRegex(ValueError, 'input'):
            driver.run_bounded(['not-a-command'], input_bytes=b'x'*(128*1024+1), output_limit=100, timeout=1)
