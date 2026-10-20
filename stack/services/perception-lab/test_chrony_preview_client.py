import unittest
from types import SimpleNamespace
from unittest.mock import patch

from chrony_preview_client import ChronyPreviewClient


class ClientTests(unittest.TestCase):
    def runtime(self, failure=False):
        config=SimpleNamespace(ntp=SimpleNamespace(server_ip='192.0.2.1',
            local_clock_quantization_ns=1000,drift_slew_ppm=1000,
            timeout_seconds=.5,valid_for_seconds=1,max_uncertainty_seconds=1))
        self.calls=[]
        def fetch(path,token):
            self.calls.append((path,token))
            return {'x-victoria-frame-metadata':'original-schema3-header'},b'original-jpeg'
        def accept(value,token,*,transport,publisher_factory):
            self.assertIs(value,config)
            publisher_factory(value)
            transport('/frame.jpg',token)
            if failure: raise ValueError('stale clock')
            return {'status':'accepted'}
        pilot=SimpleNamespace(CONFIG='fixed',load_config=lambda _:config,
            read_token=lambda:'private-test-token',fetch=fetch,accept_once=accept)
        return pilot,lambda *args:args,lambda *args:None

    def test_returns_original_header_and_pixels_only_after_acceptance(self):
        with patch('chrony_preview_client.commissioned_runtime',return_value=self.runtime()):
            self.assertEqual(ChronyPreviewClient()(),(b'original-jpeg','original-schema3-header'))
        self.assertEqual(self.calls,[('/frame.jpg','private-test-token')])

    def test_validation_failure_does_not_return_captured_bytes(self):
        with patch('chrony_preview_client.commissioned_runtime',return_value=self.runtime(True)):
            with self.assertRaisesRegex(ValueError,'stale clock'):ChronyPreviewClient()()
        self.assertEqual(len(self.calls),1)

    def test_temporary_jpeg_rejection_retries_capture_only_with_fixed_bound(self):
        for failures in (2,3):
            runtime=self.runtime();pilot=runtime[0];original=pilot.fetch;attempts=[]
            def fetch(path,token):
                attempts.append(path)
                if len(attempts)<=failures:raise ValueError('camera response rejected')
                return original(path,token)
            pilot.fetch=fetch
            with patch('chrony_preview_client.commissioned_runtime',return_value=runtime),patch('chrony_preview_client.time.sleep'):
                if failures==3:
                    with self.assertRaisesRegex(ValueError,'temporarily unavailable'):ChronyPreviewClient()()
                else:self.assertEqual(ChronyPreviewClient()(),(b'original-jpeg','original-schema3-header'))
            self.assertEqual(len(attempts),3)

    def test_shared_clock_publisher_contention_does_not_capture(self):
        lock=SimpleNamespace(acquire=lambda **kw:False)
        with patch('chrony_preview_client._CAPTURE_LOCK',lock),patch('chrony_preview_client.commissioned_runtime') as runtime:
            with self.assertRaisesRegex(ValueError,'busy'):ChronyPreviewClient()()
            runtime.assert_not_called()


if __name__=='__main__':unittest.main()
