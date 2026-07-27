from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GRANTS = ROOT / "stack/home-agent-deploy/apply-grants.sh"

FOUNDATION_MARKER = "Revision 0007 is an owner-only schema foundation"
ROLE_MARKER = "Revision 0007 provisions an expired-by-default dormant"
TABLES = (
    "reviewed_identity_migration_runs",
    "reviewed_identity_migration_source_items",
    "reviewed_identity_migration_decisions",
    "reviewed_identity_migration_item_receipts",
    "reviewed_identity_migration_finalizations",
    "legacy_identity_writer_evidence",
    "privacy_cutover_check_receipts",
    "semantic_authority_cutovers",
    "reviewed_identity_migration_erasure_impacts",
)


def foundation_section() -> str:
    source = GRANTS.read_text(encoding="utf-8")
    return source.split(FOUNDATION_MARKER, 1)[1].split(ROLE_MARKER, 1)[0]


class ApplyGrantsRevision0006aContractTests(unittest.TestCase):
    def test_absent_0007_relations_are_valid_for_the_pinned_0006a_revision(
        self,
    ) -> None:
        section = foundation_section()

        self.assertIn("target_table := pg_catalog.to_regclass(target_name)", section)
        self.assertIn("IF target_table IS NULL THEN", section)
        self.assertIn("CONTINUE;", section)
        self.assertIn("present_table_count NOT IN (", section)
        self.assertRegex(
            section,
            r"0,\s*pg_catalog\.cardinality\(candidate_tables\)",
        )
        self.assertNotRegex(
            section,
            re.compile(
                r"(?m)^REVOKE ALL PRIVILEGES ON TABLE\s*$\s*"
                r"operations\.reviewed_identity_migration_runs"
            ),
        )

    def test_present_or_partial_0007_relations_are_quarantined_fail_closed(
        self,
    ) -> None:
        section = foundation_section()

        for table_name in TABLES:
            self.assertEqual(
                section.count(f"'operations.{table_name}'"),
                2,
                table_name,
            )
        self.assertLess(
            section.index("$phase3_identity_foundation_acl$;"),
            section.index("partial Phase 3 identity authority table set"),
        )
        self.assertIn("REVOKE ALL PRIVILEGES ON TABLE %s FROM %s", section)
        self.assertIn(
            "REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), "
            "'\n        'REFERENCES (%1$s)",
            section,
        )
        for role in (
            "PUBLIC",
            "home_agent_api",
            "home_agent_binding_operator",
            "home_agent_ingest",
            "home_agent_worker",
            "home_agent_erasure",
            "home_agent_rollout",
            "home_agent_backup",
            "home_agent_identity_migration",
            "home_agent_identity_kernel",
            "home_agent_identity_finalizer",
            "home_agent_identity_finalizer_kernel",
        ):
            self.assertIn(f"'{role}'", section)

    def test_replay_guard_column_revoke_is_also_0006a_absence_safe(self) -> None:
        source = GRANTS.read_text(encoding="utf-8")
        block = source.split("DO $identity_migration_runs_column_acl$", 1)[1].split(
            "$identity_migration_runs_column_acl$;", 1
        )[0]

        self.assertIn("pg_catalog.to_regclass", block)
        self.assertIn("operations.reviewed_identity_migration_runs", block)
        self.assertIn("REVOKE UPDATE (expires_at) ON TABLE", block)


if __name__ == "__main__":
    unittest.main()
