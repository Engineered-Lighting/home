from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from config import Settings
from ownership import OwnershipError
from store import Store


class OwnershipTests(unittest.TestCase):
    def test_second_owner_cannot_run_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(Path(tmp),min_free_bytes=0)
            store=Store(settings)
            try:
                session,_=store.create_session("den","detection","Fixture","start")
                store.transition(session["id"],"starting")
                store.transition(session["id"],"recording")
                with self.assertRaises(OwnershipError):
                    Store(settings)
                script="from pathlib import Path; from config import Settings; from store import Store; from ownership import OwnershipError; import sys\ntry: Store(Settings(Path(sys.argv[1]),min_free_bytes=0))\nexcept OwnershipError: sys.exit(17)\n"
                result=subprocess.run([sys.executable,"-c",script,tmp],cwd=Path(__file__).parent,capture_output=True,timeout=5)
                self.assertEqual(result.returncode,17,result.stderr.decode())
                self.assertEqual(store.get_session(session["id"])["state"],"recording")
            finally:
                store.close()
            self.assertTrue((Path(tmp)/".controller.lock").exists())
            replacement=Store(settings)
            try:
                self.assertEqual(replacement.get_session(session["id"])["state"],"indeterminate")
            finally:
                replacement.close()

    def test_os_releases_owner_after_process_exit_without_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            script="from pathlib import Path; from config import Settings; from store import Store; import sys,os; s=Store(Settings(Path(sys.argv[1]),min_free_bytes=0)); os._exit(0)"
            result=subprocess.run([sys.executable,"-c",script,tmp],cwd=Path(__file__).parent,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            store=Store(Settings(Path(tmp),min_free_bytes=0))
            store.close()

    def test_failed_configuration_releases_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Settings(Path(tmp),min_free_bytes=0))
            store.close()
            with self.assertRaisesRegex(ValueError,"another site"):
                Store(Settings(Path(tmp),site_id="echo",min_free_bytes=0))
            replacement=Store(Settings(Path(tmp),min_free_bytes=0))
            replacement.close()


if __name__ == "__main__":
    unittest.main()
