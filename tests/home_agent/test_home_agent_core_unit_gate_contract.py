from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/home-agent-core-unit.yml"
E1_RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
CORE_TESTS = ROOT / "stack/services/home-agent-core/tests"
OPERATOR_TESTS = ROOT / "stack/home-agent-deploy/operator/tests"
CHECKOUT = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"

TRIGGERS = (
    ".github/workflows/home-agent-core-unit.yml",
    "app/src-tauri/src/**",
    "app/src/**",
    "ha-config/home_agent_edge/**",
    "stack/**",
    "tests/home_agent/test_home_agent_core_unit_gate_contract.py",
    "tools/run-home-agent-e1-postgres-gate.py",
    "web-gateway/**",
)

# Only this file is excluded from directory discovery: its fixtures need the
# live database the E1 gate provides, and it errors rather than skips without
# one.
IGNORED = "tests/test_phase3_identity_erasure_admission_postgres.py"

# Core tests whose PostgreSQL assertions skip in this gate and are not yet
# pinned in the E1 gate either. Wiring one into E1 must remove it here; a new
# test that needs a database must be pinned in E1 or listed here.
AWAITING_POSTGRES_GATE = (
    "test_app_closed_journey.py",
    "test_backup_role_grants.py",
    "test_ingest_worker_gating.py",
    "test_onboarding_status.py",
    "test_outbox_health.py",
    "test_people_directory_read.py",
    "test_people_privacy_cutover.py",
    "test_phase3_identity_authority_relations.py",
    "test_phase3_identity_authority_schema.py",
    "test_predicate_agnostic_fact_suppression.py",
    "test_principal_binding_flow.py",
    "test_principal_binding_schema.py",
    "test_rollout_authorization.py",
    "test_rollout_runtime_roles.py",
    "test_runtime_role_grants.py",
    "test_shared_link_issuance_kernel_erasure_postgres.py",
    "test_shared_link_issuance_kernel_positive_postgres.py",
    "test_worker_maintenance_schema.py",
)


def _e1_pinned_files() -> set[str]:
    """Repository paths of every test file the E1 runner passes to pytest."""

    nodes: set[str] = set()
    for call in ast.walk(ast.parse(E1_RUNNER.read_text(encoding="utf-8"))):
        if not isinstance(call, ast.Call):
            continue
        for keyword in call.keywords:
            if keyword.arg == "nodes":
                nodes |= {
                    value.value
                    for value in ast.walk(keyword.value)
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                }
    pinned: set[str] = set()
    for node in nodes:
        path = node.split("::", 1)[0]
        if path.startswith("/workspace/"):
            pinned.add(path.removeprefix("/workspace/"))
        elif path.startswith("tests/"):
            pinned.add(f"stack/services/home-agent-core/{path}")
    return pinned


class HomeAgentCoreUnitGateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.triggers, cls.jobs = cls.source.split("\njobs:\n", 1)

    def test_gate_is_github_hosted_pinned_and_read_only(self) -> None:
        self.assertEqual(self.source.count("runs-on: ubuntu-24.04"), 1)
        self.assertNotIn("self-hosted", self.source)
        self.assertEqual(len(re.findall(r"uses: ", self.source)), 1)
        self.assertEqual(self.source.count(CHECKOUT), 1)
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertIn("branches: [main, codex/home-agent-integration]", self.source)
        for heavy in ("docker run", "docker build", "docker compose", "services:"):
            self.assertNotIn(heavy, self.source)

    def test_every_trigger_covers_both_events(self) -> None:
        pull_request, push = self.triggers.split("  push:\n", 1)
        for trigger in (pull_request, push):
            declared = re.findall(r'(?m)^      - "([^"]+)"$', trigger)
            self.assertEqual(declared, list(TRIGGERS))

    def test_packages_come_from_the_hash_pinned_core_lock(self) -> None:
        for token in (
            'python3 -m venv "${RUNNER_TEMP}/home-agent-core"',
            "--require-hashes",
            "-r stack/services/home-agent-core/requirements-dev.lock",
        ):
            self.assertIn(token, self.jobs)

    def test_both_suites_are_discovered_by_directory(self) -> None:
        core = self.jobs.split("- name: Run Core unit tests", 1)[1].split("- name:", 1)[0]
        self.assertIn("working-directory: stack/services/home-agent-core", core)
        self.assertRegex(core, r"(?m)^\s+tests \\$")
        self.assertEqual(re.findall(r"--ignore=(\S+)", self.source), [IGNORED])
        self.assertNotIn("--deselect", self.source)
        self.assertNotIn(" -k ", self.source)
        operator = self.jobs.split("- name: Run operator unit tests", 1)[1]
        self.assertRegex(operator, r"(?m)^\s+stack/home-agent-deploy/operator/tests$")
        self.assertIn("git diff --exit-code", operator)

    def test_the_ignored_file_runs_in_the_e1_gate(self) -> None:
        self.assertIn(f"stack/services/home-agent-core/{IGNORED}", _e1_pinned_files())

    def test_database_tests_are_pinned_in_e1_or_tracked_here(self) -> None:
        pinned = _e1_pinned_files()
        for name in AWAITING_POSTGRES_GATE:
            path = f"stack/services/home-agent-core/tests/{name}"
            self.assertTrue((ROOT / path).is_file(), name)
            self.assertNotIn(path, pinned, f"{name} is pinned in E1; drop it here")
        untracked = []
        for directory in (CORE_TESTS, OPERATOR_TESTS):
            for path in sorted(directory.glob("test_*.py")):
                relative = path.relative_to(ROOT).as_posix()
                if relative in pinned or path.name in AWAITING_POSTGRES_GATE:
                    continue
                source = path.read_text(encoding="utf-8")
                if re.search(r"\bTEST_[A-Z0-9_]*DATABASE_URL\b", source):
                    untracked.append(relative)
        self.assertEqual(untracked, [])


if __name__ == "__main__":
    unittest.main()
