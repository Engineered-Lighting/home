from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
WORKFLOW = WORKFLOWS / "home-agent-repository-contracts.yml"
E1_RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
LOCK = ROOT / "tests/home_agent/requirements-contracts.lock"
CORE_DEV_LOCK = ROOT / "stack/services/home-agent-core/requirements-dev.lock"
CHECKOUT = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"

SUITES = (
    "tests/home_agent/test_apply_grants_admits_every_revision.py",
    "tests/home_agent/test_binding_operator_deployment_contract.py",
    "tests/home_agent/test_change_note_fragments.py",
    "tests/home_agent/test_compose_tmpfs_quoting_e5p.py",
    "tests/home_agent/test_ha_host_module_deployment.py",
    "tests/home_agent/test_home_agent_repository_contracts_gate_contract.py",
    "tests/home_agent/test_identity_migration_role_deployment_contract.py",
    "tests/home_agent/test_kernel_call_bindings_match.py",
    "tests/home_agent/test_legacy_identity_fence.py",
    "tests/home_agent/test_legacy_people_api_boundary.py",
    "tests/home_agent/test_local_pgbackrest_repository_contract.py",
    "tests/home_agent/test_phase3_activation_probe_e5ac.py",
    "tests/home_agent/test_phase3_catalog_contract_acl_order_e5o.py",
    "tests/home_agent/test_phase3_frozen_migration_ddl_e5n.py",
    "tests/home_agent/test_property_contracts_stay_fatal.py",
    "tests/home_agent/test_relationship_predicate_constraints_agree.py",
    "tests/home_agent/test_worker_lease_deployment_contract.py",
)

# Every repository path the suites above read or import, so a change to any of
# them re-runs the gate.
READ_PATHS = (
    ".github/workflows/home-agent-repository-contracts.yml",
    "changes/unreleased/**",
    "docs/HOME-AGENT-RUNBOOK.md",
    "ha-config/extended_openai_conversation/**",
    "stack/home-agent.env.example",
    "stack/home-agent-compose.yml",
    "stack/home-agent-deploy/**",
    "stack/services/home-agent-core/README.md",
    "stack/services/home-agent-core/alembic/versions/**",
    "stack/services/home-agent-core/app/config.py",
    "stack/services/home-agent-core/app/db.py",
    "stack/services/home-agent-core/app/phase3_activation_probe.py",
    "stack/services/home-agent-core/docker-entrypoint.sh",
    "tests/home_agent/requirements-contracts.lock",
    "tests/home_agent/requirements-contracts.txt",
    "tools/release/release-lib.mjs",
    "tools/run-home-agent-e1-postgres-gate.py",
)


def _pins(lock: str) -> dict[str, str]:
    return dict(re.findall(r"(?m)^([a-z0-9][a-z0-9._-]*)==([^\s\\]+)", lock))


class HomeAgentRepositoryContractsGateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.triggers, cls.jobs = cls.source.split("\njobs:\n", 1)

    def test_gate_is_github_hosted_pinned_and_read_only(self) -> None:
        self.assertEqual(self.source.count("runs-on: ubuntu-24.04"), 1)
        self.assertNotIn("self-hosted", self.source)
        self.assertEqual(self.source.count(CHECKOUT), 1)
        self.assertEqual(len(re.findall(r"uses: ", self.source)), 1)
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertIn("branches: [main, codex/home-agent-integration]", self.source)
        for heavy in ("docker run", "docker build", "docker compose", "services:"):
            self.assertNotIn(heavy, self.source)

    def test_every_suite_and_read_path_triggers_both_events(self) -> None:
        pull_request, push = self.triggers.split("  push:\n", 1)
        for path in (*SUITES, *READ_PATHS):
            quoted = f'      - "{path}"\n'
            self.assertEqual(pull_request.count(quoted), 1, path)
            self.assertEqual(push.count(quoted), 1, path)
        declared = set(re.findall(r'(?m)^      - "([^"]+)"$', self.triggers))
        self.assertEqual(declared, {*SUITES, *READ_PATHS})

    def test_every_suite_runs_under_the_hash_pinned_interpreter(self) -> None:
        run = self.jobs.split("- name: Run repository contracts", 1)[1]
        for suite in SUITES:
            self.assertEqual(run.count(f"            {suite}"), 1, suite)
        for token in (
            "--require-hashes",
            "--no-deps",
            "-r tests/home_agent/requirements-contracts.lock",
            'python3 -m venv "${RUNNER_TEMP}/home-agent-contracts"',
        ):
            self.assertIn(token, self.jobs)
        self.assertIn(
            '"${RUNNER_TEMP}/home-agent-contracts/bin/python" -m pytest', run
        )
        self.assertIn("git diff --exit-code", run)

    def test_lock_is_fully_hashed_and_matches_core_dev_lock(self) -> None:
        lock = LOCK.read_text(encoding="utf-8")
        pins = _pins(lock)
        self.assertEqual(pins.get("pytest"), "8.3.4")
        self.assertIn("pyyaml", pins)
        self.assertIn("sqlalchemy", pins)
        for block in re.split(r"(?m)^(?=[a-z0-9])", lock):
            if "==" in block.split("\n", 1)[0]:
                self.assertIn("--hash=sha256:", block, block.split("\n", 1)[0])
        core = _pins(CORE_DEV_LOCK.read_text(encoding="utf-8"))
        for name, version in pins.items():
            if name in core:
                self.assertEqual(version, core[name], name)

    def test_every_home_agent_suite_runs_in_some_workflow(self) -> None:
        referenced = E1_RUNNER.read_text(encoding="utf-8")
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            referenced += workflow.read_text(encoding="utf-8")
        orphans = [
            path.name
            for path in sorted((ROOT / "tests/home_agent").glob("test_*.py"))
            if path.name not in referenced
        ]
        self.assertEqual(orphans, [])


if __name__ == "__main__":
    unittest.main()
