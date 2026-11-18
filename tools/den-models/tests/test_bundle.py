"""Source packaging in disposable tiny Git repositories; no project commits or models."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("den_source_bundle", Path(__file__).resolve().parents[1] / "bundle.py")
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.models = self.repo / bundle.SUBTREE
        self.models.mkdir(parents=True)
        self.git("init", "--quiet")
        self.git("config", "core.autocrlf", "false")
        for name in bundle.REQUIRED:
            if name.startswith("profiles/"):
                data = json.dumps({"id": Path(name).stem, "schema": "test-source-profile/v1"})
            else:
                data = "# harmless source fixture\n"
            self.write(name, data)
        self.write("README.md", "old committed source\n")
        self.commit = self.commit_files()

    def git(self, *args, input=None):
        result = subprocess.run(["git", "-c", "user.name=Bundle Test", "-c", "user.email=bundle@example.invalid",
                                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(self.repo / "no-hooks"),
                                 "-C", str(self.repo), *args], input=input, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def write(self, name, data):
        path = self.models / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8", newline="\n")

    def commit_files(self):
        self.git("add", "--", bundle.SUBTREE)
        self.git("commit", "--quiet", "-m", "isolated source fixture")
        return self.git("rev-parse", "HEAD").decode().strip()

    def test_archive_is_deterministic_and_manifest_checks_every_payload(self):
        first, manifest, raw_manifest = bundle.build(self.repo, self.commit)
        self.assertEqual(first, bundle.build(self.repo, self.commit)[0])
        self.assertEqual(manifest["commit"], self.commit)
        self.assertEqual(set(manifest["profiles"]), {"vjepa-lm-qwen3-8b", "vjepa-lm-qwen3-4b"})
        with tarfile.open(fileobj=io.BytesIO(first), mode="r:") as archive:
            self.assertEqual(archive.extractfile(bundle.MANIFEST).read(), raw_manifest)
            checksums = archive.extractfile("SHA256SUMS").read().decode().splitlines()
            for row in checksums:
                digest, name = row.split("  ", 1)
                self.assertEqual(hashlib.sha256(archive.extractfile(name).read()).hexdigest(), digest)
            for name, record in manifest["files"].items():
                payload = archive.extractfile(bundle.SUBTREE + "/" + name).read()
                self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual(record["bytes"], len(payload))
            self.assertTrue(all(m.uid == m.gid == m.mtime == 0 and m.isfile() for m in archive.getmembers()))

    def test_selected_old_commit_never_substitutes_clean_current_worktree(self):
        self.write("README.md", "new committed source\n")
        new_commit = self.commit_files()
        self.assertNotEqual(self.commit, new_commit)
        data, manifest, _ = bundle.build(self.repo, self.commit)
        self.assertEqual(manifest["commit"], self.commit)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            self.assertEqual(archive.extractfile(bundle.SUBTREE + "/README.md").read(), b"old committed source\n")

    def test_dirty_tracked_staged_or_untracked_subtree_is_refused(self):
        self.write("README.md", "uncommitted\n")
        with self.assertRaisesRegex(bundle.BundleError, "dirty_model_source_subtree"):
            bundle.build(self.repo, self.commit)
        self.git("add", "--", bundle.SUBTREE)
        with self.assertRaisesRegex(bundle.BundleError, "dirty_model_source_subtree"):
            bundle.build(self.repo, self.commit)
        self.commit_files()
        self.write("tests/test_untracked.py", "# untracked\n")
        with self.assertRaisesRegex(bundle.BundleError, "dirty_model_source_subtree"):
            bundle.build(self.repo, self.commit)

    def test_tracked_weight_environment_and_credential_files_are_refused(self):
        for name in ("model.pt", "model.onnx", "model.safetensors", "venv/pyvenv.cfg", ".env", "credentials.json"):
            with self.subTest(name=name):
                self.write(name, "not a model or secret\n")
                commit = self.commit_files()
                with self.assertRaisesRegex(bundle.BundleError, "forbidden_source_entry"):
                    bundle.build(self.repo, commit)
                self.git("rm", "--", bundle.SUBTREE + "/" + name)
                self.git("commit", "--quiet", "-m", "remove isolated rejected fixture")

    def test_selected_tree_symlink_is_refused_even_with_clean_regular_head(self):
        name = bundle.SUBTREE + "/den_models/client.py"
        original = self.git("rev-parse", "HEAD:" + name).decode().strip()
        target = self.git("hash-object", "-w", "--stdin", input=b"/private/not-read").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "120000," + target + "," + name)
        self.git("commit", "--quiet", "-m", "isolated symlink tree fixture")
        unsafe = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "100644," + original + "," + name)
        self.git("commit", "--quiet", "-m", "restore regular source fixture")
        with self.assertRaisesRegex(bundle.BundleError, "forbidden_source_entry"):
            bundle.build(self.repo, unsafe)

    def test_only_full_commit_hashes_are_accepted(self):
        for value in ("HEAD", "main", self.commit[:12], self.commit + "~1"):
            with self.subTest(value=value), self.assertRaisesRegex(bundle.BundleError, "full_immutable_commit_required"):
                bundle.build(self.repo, value)

    def test_artifact_and_review_sidecars_are_exclusive_and_not_installed(self):
        output = Path(self.temp.name) / "review.tar"
        result = bundle.write_bundle(self.repo, self.commit, output)
        self.assertEqual(result["artifact_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(json.loads(Path(str(output) + ".manifest.json").read_text())["commit"], self.commit)
        self.assertFalse(result["installed"])
        self.assertFalse(result["services_started"])
        before = output.read_bytes()
        with self.assertRaisesRegex(bundle.BundleError, "output_already_exists"):
            bundle.write_bundle(self.repo, self.commit, output)
        self.assertEqual(output.read_bytes(), before)
        with self.assertRaisesRegex(bundle.BundleError, "outside_model_source"):
            bundle.write_bundle(self.repo, self.commit, self.models / "bad.tar")

    def test_public_control_templates_are_allowed_but_private_variants_are_not(self):
        self.assertTrue(bundle.allowed("control-producer.example.json"))
        self.assertTrue(bundle.allowed("control-producer.service.example"))
        self.assertFalse(bundle.allowed("control-producer.json"))
        self.assertFalse(bundle.allowed("den_models/credentials.py"))


if __name__ == "__main__":
    unittest.main()
