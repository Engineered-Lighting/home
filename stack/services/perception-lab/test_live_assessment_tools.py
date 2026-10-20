"""Owner CLI computes aggregates and refuses overwrite, no runtime/model I/O."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AssessmentToolTests(unittest.TestCase):
    def test_score_cli_reports_missing_requirements_and_preserves_existing_report(self):
        from test_live_assessment_scoring import IndependentAssessmentScoringTests
        measurements, references = IndependentAssessmentScoringTests().fixture()
        script = Path(__file__).resolve().parents[3] / 'tools/den-assessment/assess.py'
        with tempfile.TemporaryDirectory(prefix='authored-assessment-cli-') as work:
            directory = Path(work)
            for name, value in (('measurements', measurements), ('references', references)):
                (directory / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
            output = directory / 'report.json'
            args = [sys.executable, '-B', str(script), 'score', '--measurements', str(directory / 'measurements.json'),
                    '--references', str(directory / 'references.json'), '--output', str(output)]
            first = subprocess.run(args, capture_output=True, text=True, timeout=5)
            self.assertEqual(first.returncode, 3, first.stdout)
            self.assertEqual(json.loads(first.stdout)['decision'], 'block')
            retained = output.read_bytes()
            self.assertEqual(json.loads(retained)['work_center_error_m']['p95'], 1)
            retry = subprocess.run(args, capture_output=True, text=True, timeout=5)
            self.assertEqual(retry.returncode, 2)
            self.assertEqual(output.read_bytes(), retained)
            self.assertNotIn(str(directory), retry.stdout)


if __name__ == '__main__':
    unittest.main()
