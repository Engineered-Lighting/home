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
    "tests/home_agent/test_identity_finalizer_foundation_deployment_contract.py",
    "tests/home_agent/test_identity_migration_role_deployment_contract.py",
    "tests/home_agent/test_kernel_call_bindings_match.py",
    "tests/home_agent/test_legacy_identity_fence.py",
    "tests/home_agent/test_legacy_people_api_boundary.py",
    "tests/home_agent/test_local_pgbackrest_repository_contract.py",
    "tests/home_agent/test_native_attestation_deployment_contract.py",
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
    "ha-config/extended_openai_conversation_e4_reference/**",
    "stack/home-agent.env.example",
    "stack/home-agent-compose.yml",
    "stack/home-agent-deploy/**",
    "stack/services/home-agent-core/README.md",
    "stack/services/home-agent-core/alembic/versions/**",
    "stack/services/home-agent-core/app/api.py",
    "stack/services/home-agent-core/app/auth.py",
    "stack/services/home-agent-core/app/config.py",
    "stack/services/home-agent-core/app/db.py",
    "stack/services/home-agent-core/app/main.py",
    "stack/services/home-agent-core/app/phase3_activation_probe.py",
    "stack/services/home-agent-core/app/store.py",
    "stack/services/home-agent-core/docker-entrypoint.sh",
    "stack/services/intelligence/tests/fixtures/office_override_session_automation_tail.json",
    "tests/home_agent/requirements-contracts.lock",
    "tests/home_agent/requirements-contracts.txt",
    "tools/release/release-lib.mjs",
    "tools/test_helpers/mock_hass.py",
    "tools/run-home-agent-e1-postgres-gate.py",
    "web-gateway/server.mjs",
)

# The Home Assistant integration's in-package tests. They stub Home Assistant,
# need only the standard library, and run as scripts the way they always have.
# ha-config/extended_openai_conversation/ mirrors what LA Home Assistant runs;
# the reference directory keeps the reviewed E4/containment design testable.
HA_INTEGRATION = "ha-config/extended_openai_conversation"
HA_E4_REFERENCE = "ha-config/extended_openai_conversation_e4_reference"
IN_PACKAGE_SUITES = (
    f"{HA_INTEGRATION}/test_entity_strict.py",
    f"{HA_INTEGRATION}/test_external_routing.py",
    f"{HA_INTEGRATION}/test_frigate_proxy.py",
    f"{HA_INTEGRATION}/test_frigate_sync.py",
    f"{HA_INTEGRATION}/test_frigate_tool.py",
    f"{HA_INTEGRATION}/test_identity_store.py",
    f"{HA_INTEGRATION}/test_lifecycle.py",
    f"{HA_INTEGRATION}/test_living_lights.py",
    f"{HA_INTEGRATION}/test_native.py",
    f"{HA_INTEGRATION}/test_override_sessions.py",
    f"{HA_INTEGRATION}/test_recap.py",
    f"{HA_INTEGRATION}/test_registry.py",
    f"{HA_INTEGRATION}/test_template_helpers.py",
    f"{HA_INTEGRATION}/test_visual_preroute.py",
    f"{HA_INTEGRATION}/test_world_state.py",
    f"{HA_E4_REFERENCE}/test_action_containment.py",
    f"{HA_E4_REFERENCE}/test_cross_home_guard.py",
    f"{HA_E4_REFERENCE}/test_frigate_sync.py",
    f"{HA_E4_REFERENCE}/test_identity_store.py",
)
# Already failing on main before the integration was reconciled with LA Home
# Assistant, for reasons in the test harness itself (an exec'd slice that lost
# its `re` import; a stubbed vision call that is never reached). Listed so a new
# test cannot go unrun by accident; fixing them is separate work.
IN_PACKAGE_KNOWN_BROKEN = (
    f"{HA_INTEGRATION}/test_friendly_error_speech.py",
    f"{HA_INTEGRATION}/test_grounded_look.py",
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

    def test_every_in_package_test_runs_under_the_hash_pinned_interpreter(
        self,
    ) -> None:
        step = self.jobs.split(
            "- name: Run the Home Assistant integration in-package tests", 1
        )[1]
        listed = re.findall(r"(?m)^            (ha-config/\S+\.py)$", step)
        self.assertEqual(listed, list(IN_PACKAGE_SUITES))
        self.assertIn('for suite in "${suites[@]}"; do', step)
        self.assertIn(
            '"${RUNNER_TEMP}/home-agent-contracts/bin/python" "${suite}"', step
        )
        self.assertIn("set -euo pipefail", step)
        self.assertIn("git diff --exit-code", step)
        present = {
            path.relative_to(ROOT).as_posix()
            for directory in (HA_INTEGRATION, HA_E4_REFERENCE)
            for path in (ROOT / directory).glob("test_*.py")
        }
        self.assertEqual(present, {*IN_PACKAGE_SUITES, *IN_PACKAGE_KNOWN_BROKEN})

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
        # A name in a `paths:` trigger or in the E1 build-context list only
        # decides when a gate runs or what it can read, not what it executes.
        runner = E1_RUNNER.read_text(encoding="utf-8")
        context = runner.index("BUILD_CONTEXT_FILES = (")
        referenced = runner[:context] + runner[runner.index("\n)\n", context) :]
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            referenced += "\n".join(
                line
                for line in workflow.read_text(encoding="utf-8").splitlines()
                if not re.match(r'\s+- "[^"]+"$', line)
            )
        orphans = [
            path.name
            for path in sorted((ROOT / "tests/home_agent").glob("test_*.py"))
            if path.name not in referenced
        ]
        self.assertEqual(orphans, [])


if __name__ == "__main__":
    unittest.main()
