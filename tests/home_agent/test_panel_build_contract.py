from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
EXPECTED_DOCKER_CONTEXT = (
    "**",
    "!Dockerfile",
    "!package.json",
    "!server.mjs",
    "!src/",
    "!src/**",
)


class PanelBuildContractTests(unittest.TestCase):
    def setUp(self) -> None:
        if NODE is None:
            self.fail("Node.js is required to verify the panel build contract")
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        fixture_files = {
            "tools/build-home-agent-panel.js": (
                ROOT / "tools/build-home-agent-panel.js"
            ).read_text(encoding="utf-8"),
            "app/src/vendor/babel-7.29.0/babel.min.js": (
                "module.exports = { transform(source) { "
                'return { code: "compiled(" + source + ")" }; } };'
            ),
            "app/src/vendor/react-18.3.1/react.production.min.js": "react-runtime",
            "app/src/vendor/react-18.3.1/react-dom.production.min.js": (
                "react-dom-runtime"
            ),
            "app/src/home-agent/panel.jsx": "view()",
        }
        for relative, content in fixture_files.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

        built = self.run_builder()
        self.assertEqual(0, built.returncode, built.stderr)
        self.output = self.root / "app/src/home-agent/panel.js"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_builder(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        assert NODE is not None
        return subprocess.run(
            [NODE, str(self.root / "tools/build-home-agent-panel.js"), *arguments],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def snapshot(self) -> dict[str, tuple[bytes, int]]:
        return {
            path.relative_to(self.root).as_posix(): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_check_accepts_current_bundle_without_writing(self) -> None:
        timestamp = 1_700_000_000_000_000_000
        os.utime(self.output, ns=(timestamp, timestamp))
        before = self.snapshot()

        checked = self.run_builder("--check")

        self.assertEqual(0, checked.returncode, checked.stderr)
        self.assertIn("checked app", checked.stdout)
        self.assertEqual("", checked.stderr)
        self.assertEqual(before, self.snapshot())

    def test_check_rejects_stale_bundle_without_repairing_it(self) -> None:
        self.output.write_text("stale bundle\n", encoding="utf-8", newline="\n")
        timestamp = 1_700_000_001_000_000_000
        os.utime(self.output, ns=(timestamp, timestamp))
        before = self.snapshot()

        checked = self.run_builder("--check")

        self.assertEqual(1, checked.returncode)
        self.assertIn("is stale", checked.stderr)
        self.assertIn("build-home-agent-panel.js", checked.stderr)
        self.assertEqual(before, self.snapshot())

    def test_check_rejects_missing_bundle_without_creating_it(self) -> None:
        self.output.unlink()
        before = self.snapshot()

        checked = self.run_builder("--check")

        self.assertEqual(1, checked.returncode)
        self.assertIn("is missing or invalid", checked.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(before, self.snapshot())

    def test_unknown_arguments_fail_before_touching_the_bundle(self) -> None:
        before = self.snapshot()

        checked = self.run_builder("--check", "--repair")

        self.assertEqual(2, checked.returncode)
        self.assertIn("usage:", checked.stderr)
        self.assertEqual(before, self.snapshot())

    def test_service_docker_contexts_are_exact_allowlists(self) -> None:
        for service in ("home-agent-bff", "home-agent-origin"):
            rules = (
                ROOT / f"stack/services/{service}/.dockerignore"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(list(EXPECTED_DOCKER_CONTEXT), rules, service)


if __name__ == "__main__":
    unittest.main()
