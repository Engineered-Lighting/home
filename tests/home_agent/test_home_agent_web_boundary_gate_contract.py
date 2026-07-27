from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/home-agent-web-boundary.yml"
NODE_IMAGE = (
    "node:24-alpine@sha256:"
    "a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd"
)
NODE_LINUX_AMD64_DIGEST = (
    "sha256:4ba75f835bb8802193e4c114572113d4b26f95f6f094f4b5229d2a77773e0afc"
)
CHECKOUT = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
ATTEST = "actions/attest@f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6"
UPLOAD = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"


class HomeAgentWebBoundaryGateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.gate, cls.package = cls.source.split("  deployable-images:", 1)

    def test_gate_is_github_hosted_pinned_and_path_scoped(self) -> None:
        self.assertEqual(self.source.count("runs-on: ubuntu-24.04"), 2)
        self.assertNotIn("self-hosted", self.source)
        self.assertEqual(self.source.count(CHECKOUT), 2)
        self.assertIn("branches: [main, codex/home-agent-integration]", self.source)
        self.assertIn('".github/workflows/home-agent-web-boundary.yml"', self.source)
        self.assertIn('"stack/services/home-agent-bff/**"', self.source)
        self.assertIn('"stack/services/home-agent-origin/**"', self.source)
        self.assertIn('"stack/home-agent-deploy/agent-origin/**"', self.source)
        self.assertEqual(
            self.source.count('"tests/home_agent/test_panel_build_contract.py"'),
            2,
        )
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertIn(f"NODE_IMAGE: {NODE_IMAGE}", self.source)
        self.assertIn(
            f"NODE_LINUX_AMD64_DIGEST: {NODE_LINUX_AMD64_DIGEST}",
            self.source,
        )

    def test_pinned_node_index_is_resolved_to_the_exact_amd64_child(self) -> None:
        self.assertEqual(
            self.source.count(
                'docker buildx imagetools inspect "${NODE_IMAGE}" --raw'
            ),
            2,
        )
        for token in (
            'item.get("platform", {}).get("os") == "linux"',
            'item.get("platform", {}).get("architecture") == "amd64"',
            'expected = os.environ["NODE_LINUX_AMD64_DIGEST"]',
            'if digests != [expected]:',
            "pinned Node Linux AMD64 manifest mismatch",
        ):
            self.assertEqual(self.source.count(token), 2)

    def test_source_gate_runs_every_reviewed_boundary_without_network(self) -> None:
        for token in (
            "--network none",
            "--read-only",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--memory 256m",
            "--pids-limit 64",
            "node tools/build-home-agent-panel.js --check",
            "node tools/run-home-security-tests.js",
            "node tools/run-native-agent-security-tests.js",
            "node tools/run-agent-origin-boundary-tests.mjs",
            "node tools/run-native-agent-gateway-tests.js",
            "node tools/run-web-gateway-path-security-tests.mjs",
            "node tools/run-web-gateway-stack-token-tests.js",
            "npm --prefix stack/services/home-agent-bff test",
            "npm --prefix stack/services/home-agent-origin test",
            "test_network_contract.py",
            "test_home_agent_web_boundary_gate_contract.py",
            "tests/home_agent/test_panel_build_contract.py",
            "git diff --exit-code -- app/src/home-agent/panel.js",
        ):
            self.assertIn(token, self.gate)

    def test_both_linux_amd64_images_are_built_and_isolated_smoked(self) -> None:
        self.assertGreaterEqual(self.source.count("--platform linux/amd64"), 4)
        self.assertGreaterEqual(self.source.count("--provenance=false"), 4)
        self.assertGreaterEqual(
            self.source.count("--build-context agent_assets=app/src/home-agent"),
            2,
        )
        self.assertGreaterEqual(self.source.count("docker network create --internal"), 2)
        self.assertNotIn("--publish", self.source)
        self.assertIsNone(
            re.search(r"(?:^|\s)-p\s+(?:[0-9]|\$\{|127\.)", self.source)
        )
        for token in (
            "/home-agent/index.html",
            '"/", "/healthz", "/native-oauth-client", '
            '"/api/agent/native/v1/snapshot"',
            "/api/agent/auth/session",
            "npm run verify-network",
        ):
            self.assertIn(token, self.source)
        self.assertEqual(self.source.count("docker ps -aq --filter"), 2)
        self.assertEqual(self.source.count("docker network ls -q --filter"), 2)
        self.assertEqual(
            self.source.count(
                '"strict-transport-security": "max-age=31536000"'
            ),
            2,
        )
        self.assertEqual(
            self.source.count(
                '"permissions-policy": '
                '"camera=(), microphone=(), geolocation=(), payment=(), usb=()"'
            ),
            2,
        )

    def test_bff_image_smokes_use_the_production_persistence_contract(self) -> None:
        for forbidden in (
            "NODE_ENV=test",
            "HOME_AGENT_ALLOW_",
            "HOME_AGENT_SESSION_ENCRYPTION_KEY=",
            "HOME_AGENT_CORE_TOKEN=",
            "HOME_AGENT_HA_URL=http://",
        ):
            self.assertNotIn(forbidden, self.source)
        for required in (
            "-e NODE_ENV=production",
            "-e HOME_AGENT_SESSION_DB_PATH=/sessions/sessions.sqlite",
            "-e HOME_AGENT_SESSION_ENCRYPTION_KEY_FILE="
            "/run/secrets/session_encryption_key",
            "-e HOME_AGENT_CORE_TOKEN_FILE=/run/secrets/service_token",
            "-e HOME_AGENT_HA_URL=https://ha.gate.invalid",
            "--tmpfs /sessions:rw,nosuid,nodev,noexec,size=16m,"
            "mode=0700,uid=1000,gid=1000",
            "--mount \"type=bind,src=${secret_dir}/session_encryption_key,"
            "dst=/run/secrets/session_encryption_key,readonly\"",
            "--mount \"type=bind,src=${secret_dir}/service_token,"
            "dst=/run/secrets/service_token,readonly\"",
            'docker exec "${bff}" node --input-type=module',
            'const database = fs.statSync("/sessions/sessions.sqlite");',
            "database.uid !== 1000 || (database.mode & 0o777) !== 0o600",
        ):
            self.assertEqual(self.source.count(required), 2, required)
        self.assertEqual(
            self.source.count(
                "left its temporary secret directory behind"
            ),
            2,
        )

    def test_deployable_artifacts_are_main_only_attested_and_bounded(self) -> None:
        for token in (
            "github.ref == 'refs/heads/main'",
            "github.event_name == 'push'",
            "github.event_name == 'workflow_dispatch'",
            "id-token: write",
            "attestations: write",
            "artifact-metadata: write",
            ATTEST,
            UPLOAD,
            "home-agent-bff-linux-amd64.tar.gz",
            "home-agent-origin-linux-amd64.tar.gz",
            "manifest.json",
            "SHA256SUMS",
            "provenance.sigstore.json",
            "source_commit",
            "image_id",
            'docker save "${BFF_IMAGE}"',
            'docker save "${ORIGIN_IMAGE}"',
            "tarfile.open(path, mode=\"r:gz\")",
            '(os.environ["BFF_IMAGE"],)',
            '(os.environ["ORIGIN_IMAGE"],)',
            'os.environ["BFF_DEPLOYMENT_IMAGE"]',
            'os.environ["ORIGIN_DEPLOYMENT_IMAGE"]',
            "retention-days: 14",
            "compression-level: 0",
        ):
            self.assertIn(token, self.package)
        self.assertIn(
            'if configs != {expected_config} or tags != set(expected_tags):',
            self.package,
        )
        self.assertIn(
            "image identity or tag contract mismatch",
            self.package,
        )
        self.assertIn(
            "BFF_DEPLOYMENT_IMAGE: engineered-lighting/home-agent-bff:local",
            self.source,
        )
        self.assertIn(
            "ORIGIN_DEPLOYMENT_IMAGE: engineered-lighting/home-agent-origin:local",
            self.source,
        )
        self.assertNotIn("docker tag ", self.package)
        self.assertNotIn("${BFF_DEPLOYMENT_IMAGE}", self.package)
        self.assertNotIn("${ORIGIN_DEPLOYMENT_IMAGE}", self.package)
        self.assertEqual(
            self.package.count('os.environ["BFF_DEPLOYMENT_IMAGE"]'),
            1,
        )
        self.assertEqual(
            self.package.count('os.environ["ORIGIN_DEPLOYMENT_IMAGE"]'),
            1,
        )
        self.assertEqual(
            self.source.count("          cleanup\n          trap - EXIT"),
            2,
        )
        self.assertNotIn("packages: write", self.package)
        self.assertNotIn("${{ secrets.", self.package)

    def test_service_build_contexts_and_base_are_immutable(self) -> None:
        expected_context = [
            "**",
            "!Dockerfile",
            "!package.json",
            "!server.mjs",
            "!src/",
            "!src/**",
        ]
        for service in ("home-agent-bff", "home-agent-origin"):
            directory = ROOT / "stack/services" / service
            dockerfile = (directory / "Dockerfile").read_text(encoding="utf-8")
            self.assertEqual(dockerfile.splitlines()[0], f"FROM {NODE_IMAGE}")
            self.assertEqual(
                (directory / ".dockerignore").read_text(encoding="utf-8").splitlines(),
                expected_context,
            )


if __name__ == "__main__":
    unittest.main()
