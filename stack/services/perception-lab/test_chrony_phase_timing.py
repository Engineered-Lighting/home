import unittest
from types import SimpleNamespace
from unittest.mock import patch

from chrony_preview_client import ChronyPreviewClient


class PhaseTimingTests(unittest.TestCase):
    def runtime(self, accept):
        config=SimpleNamespace(ntp=SimpleNamespace(server_ip='192.0.2.1',
            local_clock_quantization_ns=1000,drift_slew_ppm=1000,
            timeout_seconds=.5,valid_for_seconds=1,max_uncertainty_seconds=1))
        def fetch(path,token):
            return {'x-victoria-frame-metadata':'header'},b'jpeg'
        pilot=SimpleNamespace(CONFIG='fixed',load_config=lambda _:config,
            read_token=lambda:'private-test-token',fetch=fetch,accept_once=accept)
        return pilot,lambda *args:args,lambda *args:None

    def test_supported_hook_records_phase_names_and_durations_only(self):
        def accept(value,token,*,transport,publisher_factory,on_phase=lambda phase:None):
            for phase in ('initial_health','jpeg_request','Private Path/1',None,'final_health'):
                on_phase(phase)
            transport('/frame.jpg',token)
            return {'status':'accepted'}
        client=ChronyPreviewClient()
        with patch('chrony_preview_client.commissioned_runtime',return_value=self.runtime(accept)):
            self.assertEqual(client(),(b'jpeg','header'))
        self.assertEqual(set(client.last_phases_ms),{'initial_health','jpeg_request','final_health'})
        self.assertTrue(all(v>=0 for v in client.last_phases_ms.values()))

    def test_failed_acceptance_still_reports_reached_phases(self):
        def accept(value,token,*,transport,publisher_factory,on_phase):
            on_phase('initial_health');on_phase('jpeg_timing_validation')
            raise ValueError('camera timing acceptance failed')
        client=ChronyPreviewClient()
        with patch('chrony_preview_client.commissioned_runtime',return_value=self.runtime(accept)):
            with self.assertRaisesRegex(ValueError,'timing acceptance'):client()
        self.assertEqual(list(client.last_phases_ms),['initial_health','jpeg_timing_validation'])

    def test_older_commissioned_runtime_is_called_unchanged(self):
        def accept(value,token,*,transport,publisher_factory):
            transport('/frame.jpg',token)
            return {'status':'accepted'}
        client=ChronyPreviewClient()
        with patch('chrony_preview_client.commissioned_runtime',return_value=self.runtime(accept)):
            self.assertEqual(client(),(b'jpeg','header'))
        self.assertIsNone(client.last_phases_ms)


if __name__=='__main__':unittest.main()
