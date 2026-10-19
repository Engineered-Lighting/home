"""Existing endpoint originals through the actual worker and ordinary replay.

Only camera/model/provider responses and independent commissioning are authored;
the new source family, Store, journal, worker and private replay paths are real.
"""
import unittest

import test_lab_existing_api_desk_runtime as existing
import test_lab_stereo_worker_runtime as worker_fixture


class ExistingApiWorkerRuntimeTests(worker_fixture.LabStereoWorkerRuntimeTests):
    def make_source(self):
        return existing.ExistingApiDeskRuntimeTests().make_source()


if __name__ == '__main__':
    unittest.main()
