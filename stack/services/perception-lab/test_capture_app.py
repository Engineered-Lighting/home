import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from capture_app import load_provisioning


class CaptureProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.settings=self.root/'settings.json';self.secret=self.root/'camera-token';self.config=self.root/'capture.json'
        self.secret.write_text('fixture-token-'+'a'*32)
        self.settings.write_text(json.dumps({'storage_root':str(self.root/'not-created'),'site_id':'victoria',
            'cameras':[{'id':'den'}],'token_grants':[{'subject':'fixture','site_id':'victoria',
                'token_sha256':hashlib.sha256(b'fixture').hexdigest(),'capabilities':['read','record']}]}))
        self.value={'schema_version':1,'settings_file':str(self.settings),'camera_credential_file':str(self.secret),
            'source':{'id':'oak','camera_id':'den','url':'rtsp://127.0.0.1:8554/oak','codec':'avc1.640028','codec_validation_id':'fixture'},
            'receiver_clock':{'validation_id':'fixture','uncertainty_seconds':.1},
            'gate_references':{'technical':'fixture','health_resource':'fixture','workload_authorization':'fixture'}}
    def tearDown(self):self.tmp.cleanup()
    def load(self):
        self.config.write_text(json.dumps(self.value))
        return load_provisioning(self.config)
    def test_configuration_load_is_inert_and_token_callback_rereads_file(self):
        with patch('subprocess.Popen',side_effect=AssertionError('process')),patch('http.client.HTTPConnection',side_effect=AssertionError('network')):
            settings,source,clock,credential,gates=self.load()
        self.assertFalse(Path(settings.storage_root).exists())
        self.assertEqual(source.ffmpeg,'/usr/bin/ffmpeg')
        self.assertEqual(credential(),'fixture-token-'+'a'*32)
        self.secret.write_text('rotated-fixture-'+'b'*32)
        self.assertEqual(credential(),'rotated-fixture-'+'b'*32)
        self.assertNotIn('fixture-token',repr(gates))
    def test_encoded_only_needs_no_jpeg_credential_or_clock(self):
        self.value['capture_mode']='encoded_only'
        self.value.pop('camera_credential_file');self.value.pop('receiver_clock')
        self.secret.unlink()
        settings,source,clock,credential,gates=self.load()
        self.assertIsNone(clock);self.assertIsNone(credential)
        self.assertEqual(source.camera_id,'den')
        self.assertFalse(Path(settings.storage_root).exists())
        self.value['receiver_clock']={'validation_id':'unexpected'}
        with self.assertRaises(ValueError):self.load()
    def test_invalid_endpoint_model_fields_or_missing_gate_fail_before_storage(self):
        for key,value in (('url','rtsp://192.168.0.100:8554/oak'),('ffmpeg','caller-program'),('camera_id','other')):
            original=dict(self.value['source']);self.value['source'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.load()
            self.value['source']=original
        self.value['gate_references'].pop('workload_authorization')
        with self.assertRaises(ValueError):self.load()
        self.assertFalse((self.root/'not-created').exists())
    def test_malformed_secret_and_duplicate_fields_have_content_free_errors(self):
        self.secret.write_text('do-not-print\r\nsecret')
        with self.assertRaisesRegex(ValueError,'^camera credential unavailable$'):self.load()
        self.config.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError,'^capture configuration unreadable or invalid$'):load_provisioning(self.config)


if __name__=='__main__':unittest.main()
