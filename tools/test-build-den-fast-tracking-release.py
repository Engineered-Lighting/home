import importlib.util,io,json,pathlib,tempfile,tarfile,unittest
spec=importlib.util.spec_from_file_location('release',pathlib.Path(__file__).with_name('build-den-fast-tracking-release.py'))
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
class ReleaseTests(unittest.TestCase):
 def test_reproducible_source_closure_contains_no_tests_media_or_credentials(self):
  with tempfile.TemporaryDirectory() as temp:
   root=pathlib.Path(temp);a=release.build(root/'a','1'*40);b=release.build(root/'b','1'*40)
   self.assertEqual(a['archive_sha256'],b['archive_sha256'])
   with tarfile.open(a['archive']) as tar:
    names=tar.getnames();self.assertEqual(len(names),59)
    self.assertTrue(all(name=='manifest.json' or name.startswith('service/') and name.endswith('.py') for name in names))
    self.assertFalse(any('test_' in name or 'token' in name for name in names))
    m=json.load(tar.extractfile('manifest.json'));self.assertEqual(m['entrypoints']['la_detector_app'],'create_fast_tracking_app')
    self.assertEqual(m['required_worker_environment']['YOLO_AUTOINSTALL'],'false')
    self.assertIn('service/fast_tracking_depth.py',names)
 def test_new_unreviewed_local_import_is_refused(self):
  with tempfile.TemporaryDirectory() as temp:
   root=pathlib.Path(temp)
   for name,raw in release.sources().items():(root/pathlib.Path(name).name).write_bytes(raw)
   path=root/'fast_tracking_camera_entry.py';path.write_bytes(path.read_bytes()+b'\nimport unreviewed_model\n')
   (root/'unreviewed_model.py').write_text('')
   with self.assertRaisesRegex(ValueError,'unreviewed local import'):release.sources(root)
if __name__=='__main__':unittest.main()
