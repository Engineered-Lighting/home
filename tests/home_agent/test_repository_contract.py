from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

GOLDEN_SCENARIOS = (
    ("complete-itaipava", "Complete Itaipava travel and memory story"),
    ("stale", "Stale Itaipava location"),
    ("jitter", "GPS boundary jitter"),
    ("app-closed", "App closed throughout departure and arrival"),
    ("correction", "Place correction"),
    ("forget", "Place-memory forgetting"),
    ("association-not-presence", "Association does not imply presence"),
    ("kitchen", "Kitchen investigation"),
    ("lighting", "Manual lighting correction"),
    ("rooms", "Multiple occupants in different rooms"),
    ("guest", "Unknown guest"),
    ("camera-injection", "Camera prompt injection"),
    ("retrieved-injection", "Retrieved-memory prompt injection"),
    ("ai-box-outage", "AI box unavailable"),
    ("ha-reconnect", "Home Assistant disconnect and reconnect"),
    ("recorder-gap", "Recorder history incomplete"),
    ("duplicate-evidence", "Duplicate person and device-tracker evidence"),
    ("context-governance", "Context compaction with governance preserved"),
    ("circular-summaries", "Circular summary self-corroboration attempt"),
    ("cascade-deletion", "Cascade deletion through embeddings and initiatives"),
    ("checkpoint-residual", "Deleted clip already used in a model checkpoint"),
    ("shared-screen-privacy", "Shared-screen location privacy"),
    ("people-conflict", "Conflicting People Graph relationships"),
    ("blind-spot-absence", "Camera blind spot and invalid absence inference"),
)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def python_callables(relative: str) -> dict[str, ast.AST]:
    source = read(relative)
    tree = ast.parse(source, filename=relative)
    result: dict[str, ast.AST] = {}

    def collect(body: list[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                collect(node.body, (*prefix, node.name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = (*prefix, node.name)
                result["::".join(qualified)] = node
                collect(node.body, qualified)

    collect(tree.body)
    return result


class RepositoryBoundaryTests(unittest.TestCase):
    def test_itaipava_golden_manifest_has_exactly_24_verifiable_scenarios(
        self,
    ) -> None:
        manifest = json.loads(
            read("tests/home_agent/itaipava_golden_scenarios.json")
        )
        scenarios = manifest["scenarios"]
        expected = list(GOLDEN_SCENARIOS)
        actual = [(item["id"], item["title"]) for item in scenarios]

        self.assertEqual(1, manifest["schema_version"])
        self.assertEqual("itaipava-mvp-golden-v1", manifest["suite"])
        self.assertEqual(24, len(scenarios))
        self.assertEqual(24, len({item["id"] for item in scenarios}))
        self.assertEqual(expected, actual)

        policy = json.loads(
            read("stack/home-agent-deploy/policy/home-agent-mvp-v1.json")
        )
        disabled = set(policy["disabled_capabilities"])
        callable_cache: dict[str, dict[str, ast.AST]] = {}

        def resolve(node_reference: str) -> ast.AST:
            parts = node_reference.split("::")
            self.assertGreaterEqual(len(parts), 2, node_reference)
            relative, qualified = parts[0], "::".join(parts[1:])
            self.assertTrue((ROOT / relative).is_file(), node_reference)
            callables = callable_cache.setdefault(
                relative, python_callables(relative)
            )
            self.assertIn(qualified, callables, node_reference)
            return callables[qualified]

        for scenario in scenarios:
            status = scenario["status"]
            evidence = scenario["evidence"]
            self.assertIn(status, {"covered", "capability_disabled"})
            self.assertIsInstance(evidence, list)

            python_evidence = []
            disabled_evidence = []
            for item in evidence:
                kind = item["kind"]
                if kind == "python_test":
                    node = resolve(item["node"])
                    self.assertTrue(node.name.startswith("test_"), item["node"])
                    python_evidence.append(item)
                elif kind == "capability_disabled":
                    self.assertEqual("capability_disabled", item["outcome"])
                    self.assertIn(item["policy_key"], disabled)
                    handler = resolve(item["handler"])
                    handler_source = ast.get_source_segment(
                        read(item["handler"].split("::", 1)[0]), handler
                    )
                    self.assertIn("CapabilityDisabledError", handler_source or "")
                    disabled_evidence.append(item)
                else:
                    self.fail(
                        f"{scenario['id']} has unsupported evidence kind {kind!r}"
                    )

            if status == "covered":
                self.assertEqual("test_passes", scenario["expected_outcome"])
                self.assertTrue(python_evidence, scenario["id"])
                self.assertNotIn("gap", scenario)
            elif status == "capability_disabled":
                self.assertEqual(
                    "capability_disabled", scenario["expected_outcome"]
                )
                self.assertTrue(disabled_evidence, scenario["id"])
                self.assertNotIn("gap", scenario)
            self.assertNotIn("gap", scenario)

    def test_bff_and_core_share_only_typed_web_contracts(self) -> None:
        bff = read("stack/services/home-agent-bff/src/bff.mjs")
        api = read("stack/services/home-agent-core/app/api.py")
        for route in (
            "/snapshot",
            "/memory-transactions",
            "/memory-transactions/{transaction_id}/confirm",
            "/facts/{fact_id}/forget-preview",
            "/erasure-requests/{request_id}/confirm",
        ):
            self.assertIn(route, api)
        self.assertIn("X-Authenticated-HA-User", bff)
        self.assertNotIn('prefix: "/proxy/intelligence"', bff)
        browser_routes = bff.split("const ROUTES", 1)[1].split("const NATIVE_ROUTES", 1)[0]
        native_routes = bff.split("const NATIVE_ROUTES", 1)[1]
        self.assertNotIn("v1\\/initiatives", browser_routes)
        self.assertIn("v1\\/initiatives", native_routes)
        self.assertIn("X-Home-Agent-Channel", bff)

    def test_agent_surface_uses_typed_descriptor_lifecycle_only(self) -> None:
        client = read("app/src/home-agent/api.js")
        panel = read("app/src/home-agent/panel.jsx")
        for operation in (
            "previewCorrection",
            "confirmCorrection",
            "previewRetraction",
            "confirmRetraction",
            "previewForget",
            "confirmForget",
        ):
            self.assertIn(operation, client)
        self.assertNotIn("/actions", client)
        self.assertIn("claimInitiative", client)
        self.assertIn("queryParentPresence", client)
        self.assertNotIn('request("/api/agent/v1/initiatives', client)
        self.assertIn("descriptor-only forgetting", panel)

    def test_whoami_and_oauth_paths_are_consistent(self) -> None:
        edge = read("ha-config/home_agent_edge/const.py")
        edge_setup = read("ha-config/home_agent_edge/__init__.py")
        bff = read("stack/services/home-agent-bff/src/bff.mjs")
        self.assertIn('WHOAMI_URL = "/api/home_agent_edge/whoami"', edge)
        self.assertIn('getattr(user, "is_active", False)', edge_setup)
        self.assertIn("/api/home_agent_edge/whoami", bff)
        self.assertIn("/api/agent/auth/callback", bff)
        self.assertIn("OAUTH_COOKIE_NAME", bff)

    def test_edge_raw_spool_defaults_outside_ha_backup_roots(self) -> None:
        edge = read("ha-config/home_agent_edge/const.py")
        flow = read("ha-config/home_agent_edge/config_flow.py")
        self.assertIn('DEFAULT_SPOOL_PATH = "/tmp/home_agent_edge/runtime.sqlite"', edge)
        for root in ('Path("/config")', 'Path("/backup")', 'Path("/share")'):
            self.assertIn(root, flow)
        self.assertIn("spool_path_must_be_backup_excluded", flow)
        transport = read("ha-config/home_agent_edge/transport.py")
        crypto = read("ha-config/home_agent_edge/crypto.py")
        outbox = read("ha-config/home_agent_edge/outbox.py")
        self.assertIn('(\"client key\", client_key_file)', transport)
        self.assertIn("must be mode 0600 or stricter", transport)
        self.assertIn("spool key file must be mode 0600 or stricter", crypto)
        self.assertIn("spool directory must be mode 0700 or stricter", outbox)
        self.assertIn("os.O_EXCL", outbox)

    def test_dedicated_deployment_keeps_authorities_unpublished(self) -> None:
        compose = read("stack/home-agent-compose.yml")
        postgres_block = compose.split("  postgres:", 1)[1].split("\n  backup-gate:", 1)[0]
        for name in ("core-api", "core-ingest", "core-worker"):
            match = re.search(
                rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
                compose,
            )
            self.assertIsNotNone(match)
            block = match.group(1)
            self.assertNotIn("ports:", block)
        self.assertNotIn("ports:", postgres_block)
        self.assertIn("HOME_AGENT_RUNTIME_ROOT", compose)
        self.assertIn("HOME_AGENT_DATA_ROOT", compose)
        self.assertEqual(compose.count("/run/pgbackrest-sftp/id_ed25519:ro"), 2)
        self.assertEqual(compose.count("/run/pgbackrest-sftp/id_ed25519.pub:ro"), 2)
        self.assertIn(
            "pgbackrest --stanza=home-agent check",
            read("stack/home-agent-deploy/backup-gate.sh"),
        )
        self.assertIn("ssl_verify_client on", read("stack/home-agent-deploy/nginx-edge.conf"))

    def test_edge_ingress_exposes_only_exact_typed_mtls_routes(self) -> None:
        nginx = read("stack/home-agent-deploy/nginx-edge.conf")
        policy_location = re.search(
            r"(?ms)^\s*location = /v1/ingest/privacy-policy \{(.*?)^\s*\}",
            nginx,
        )
        self.assertIsNotNone(policy_location)
        policy_block = policy_location.group(1)
        self.assertIn("$request_method != GET", policy_block)
        self.assertIn("proxy_set_header Authorization $http_authorization", policy_block)
        self.assertIn("proxy_pass http://core-ingest:8104", policy_block)

        envelope_location = re.search(
            r"(?ms)^\s*location = /v1/ingest/envelopes \{(.*?)^\s*\}",
            nginx,
        )
        self.assertIsNotNone(envelope_location)
        self.assertIn("limit_except POST", envelope_location.group(1))
        self.assertIn("location / { return 404; }", nginx)

    def test_deployment_secrets_are_role_minimized(self) -> None:
        compose = read("stack/home-agent-compose.yml")

        def service_block(name: str) -> str:
            match = re.search(
                rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
                compose,
            )
            self.assertIsNotNone(match)
            return match.group(1)

        self.assertIn("knowledge_encryption_key", service_block("core-api"))
        self.assertIn("knowledge_encryption_key", service_block("core-ingest"))
        self.assertNotIn("knowledge_encryption_key", service_block("core-worker"))
        self.assertIn("session_encryption_key", service_block("bff"))
        self.assertIn('user: "999:999"', service_block("backup-gate"))
        self.assertNotIn("/master/", compose)

        materializer = read("stack/home-agent-deploy/materialize-secrets.sh")
        self.assertIn("install -m 0400", materializer)
        self.assertIn("core-api knowledge_encryption_key", materializer)
        self.assertNotRegex(materializer, r"core-worker knowledge_encryption_key")

        grants = read("stack/home-agent-deploy/apply-grants.sh")
        self.assertIn("identity.privacy_directives", grants)

    def test_backup_gate_cannot_impersonate_database_owner(self) -> None:
        compose = read("stack/home-agent-compose.yml")

        def service_block(name: str) -> str:
            match = re.search(
                rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
                compose,
            )
            self.assertIsNotNone(match)
            return match.group(1)

        postgres = service_block("postgres")
        backup = service_block("backup-gate")
        self.assertIn("--auth-local=scram-sha-256", postgres)
        self.assertNotIn("--auth-local=trust", postgres)
        self.assertIn("hba_file=/etc/pg_hba-home-agent.conf", postgres)
        hba = read("stack/home-agent-deploy/postgres-pg_hba.conf")
        self.assertNotRegex(hba, r"(?m)^\s*(?:local|host)\s+.*\btrust\b")
        self.assertRegex(hba, r"(?m)^local\s+all\s+home_agent_backup\s+reject$")

        self.assertIn("postgres_backup_password_backup_gate", backup)
        self.assertIn("/deploy/backup-gate.sh", backup)
        self.assertNotIn("home_agent_owner", backup)
        self.assertIn("read_only: true", backup)
        self.assertIn("cap_drop: [ALL]", backup)

        pgbackrest = read("stack/home-agent-deploy/pgbackrest.conf.example")
        self.assertIn("pg1-user=home_agent_backup", pgbackrest)
        self.assertIn("pg1-database=home_agent", pgbackrest)
        self.assertNotIn("pg1-user=home_agent_owner", pgbackrest)
        self.assertIn("repo1-type=sftp", pgbackrest)
        self.assertIn("repo1-sftp-host-key-check-type=fingerprint", pgbackrest)
        self.assertIn("repo1-sftp-host-key-hash-type=sha256", pgbackrest)
        self.assertIn("repo1-cipher-type=aes-256-cbc", pgbackrest)

        preflight = read("stack/home-agent-deploy/preflight.sh")
        self.assertIn("HOME_AGENT_PGBACKREST_SFTP_KEY", preflight)
        self.assertIn("HOME_AGENT_PGBACKREST_SFTP_PUBLIC_KEY", preflight)
        self.assertIn("not on a verified /dev/mapper encrypted mount", preflight)
        self.assertIn("'^repo1-type=sftp$'", preflight)
        self.assertIn("'^repo1-sftp-host-key-check-type=fingerprint$'", preflight)
        self.assertIn("'^repo1-sftp-host-fingerprint=[0-9a-f]{64}$'", preflight)
        self.assertIn("'^repo1-cipher-type=aes-256-cbc$'", preflight)
        self.assertIn("'^repo1-cipher-pass=[0-9a-f]{64}$'", preflight)

        roles = read("stack/home-agent-deploy/provision-roles.sh")
        runtime_grants = read("stack/home-agent-deploy/apply-grants.sh")
        self.assertIn("pg_read_all_data, pg_write_all_data", roles)
        self.assertIn("pg_read_server_files, pg_write_server_files", roles)
        self.assertIn("pg_checkpoint, pg_maintain, pg_signal_backend", roles)
        self.assertNotIn("GRANT pg_read_all_stats", roles)
        self.assertNotIn("GRANT pg_read_all_data TO home_agent_backup", runtime_grants)
        for signature in (
            "pg_catalog.pg_backup_start(text, boolean)",
            "pg_catalog.pg_backup_stop(boolean)",
            "pg_catalog.pg_switch_wal()",
            "pg_catalog.pg_create_restore_point(text)",
        ):
            self.assertIn(signature, roles)

    def test_erasure_ledger_mounts_and_operator_audience_fail_closed(self) -> None:
        compose = read("stack/home-agent-compose.yml")

        def service_block(name: str) -> str:
            match = re.search(
                rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
                compose,
            )
            self.assertIsNotNone(match)
            return match.group(1)

        for name in ("core-api", "core-ingest"):
            block = service_block(name)
            self.assertIn("HOME_AGENT_ERASURE_LEDGER_ROOT", block)
            self.assertIn("target: /erasure-ledger", block)
            self.assertIn("read_only: true", block)
            self.assertNotIn("source: \"${HOME_AGENT_ERASURE_LEDGER_ROOT}/ledger.head.json\"", block)
        self.assertNotIn("read_only: true", service_block("core-worker").split("target: /erasure-ledger", 1)[-1][:80])
        self.assertIn("network_mode: none", service_block("ledger-init"))
        self.assertIn("operator_token_identity_migration", service_block("identity-migration"))
        self.assertNotIn("service_token_identity_migration", service_block("identity-migration"))

        preflight = read("stack/home-agent-deploy/preflight.sh")
        self.assertIn("explicit operator init required", read("stack/services/home-agent-core/app/ledger.py"))
        self.assertIn("one-time ledger-init profile", preflight)
        self.assertIn("assert_separate_roots", preflight)

        # Directory consumers follow an atomically replaced head pathname;
        # a single-file bind would remain pinned to the original inode.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            head = root / "ledger.head.json"
            head.write_text("old", encoding="utf-8")
            replacement = root / ".head.tmp"
            replacement.write_text("new", encoding="utf-8")
            replacement.replace(head)
            self.assertEqual((root / "ledger.head.json").read_text(), "new")

    def test_locked_policy_disables_expansive_capabilities(self) -> None:
        policy = json.loads(read("stack/home-agent-deploy/policy/home-agent-mvp-v1.json"))
        self.assertFalse(policy["location_memory_default"])
        self.assertFalse(policy["travel_greetings_default"])
        self.assertFalse(policy["model_physical_actions"])
        self.assertFalse(policy["external_routing_for_private_data"])
        self.assertFalse(policy["legacy_intelligence_authority"])
        self.assertEqual(policy["raw_observation_ttl_seconds"], 86_400)
        self.assertEqual(policy["disk_warn_free_percent"], 20)
        self.assertEqual(policy["disk_stop_optional_free_percent"], 15)
        self.assertEqual(policy["disk_read_only_free_percent"], 10)
        self.assertEqual(policy["ingest_daily_event_alert"], 1_000)
        self.assertEqual(policy["ingest_weekly_byte_alert"], 100 * 1024 * 1024)

    def test_resource_budget_is_deterministic_and_not_env_tunable(self) -> None:
        resources = read("stack/services/home-agent-core/app/resources.py")
        compose = read("stack/home-agent-compose.yml")
        self.assertIn("DISK_WARN_FREE_PERCENT = 20.0", resources)
        self.assertIn("DISK_STOP_OPTIONAL_FREE_PERCENT = 15.0", resources)
        self.assertIn("DISK_READ_ONLY_FREE_PERCENT = 10.0", resources)
        self.assertIn("INGEST_DAILY_EVENT_ALERT = 1_000", resources)
        self.assertIn("INGEST_WEEKLY_BYTE_ALERT = 100 * 1024 * 1024", resources)
        self.assertIn("HOME_AGENT_STORAGE_MONITOR_PATH: /storage-monitor/durable", compose)
        self.assertIn("target: /storage-monitor/durable", compose)

    def test_stale_host_env_cannot_reenable_legacy_content_capture(self) -> None:
        compose = read("stack/docker-compose.yml")

        def service_block(name: str) -> str:
            match = re.search(
                rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
                compose,
            )
            self.assertIsNotNone(match)
            return match.group(1)

        metrics = service_block("metrics-sidecar")
        self.assertIn('METRICS_CONVERSATION_CONTENT_ENABLED: "0"', metrics)
        self.assertNotIn("${METRICS_CONVERSATION_CONTENT_ENABLED", metrics)
        self.assertNotIn("TRACES_DIR", metrics)
        self.assertNotIn("metrics-sidecar/data:/app/data", metrics)

        metrics_source = read("stack/services/metrics-sidecar/main.py")
        self.assertIn("CONVERSATION_CONTENT_ENABLED = False", metrics_source)
        self.assertNotIn('os.environ.get("METRICS_CONVERSATION_CONTENT_ENABLED"', metrics_source)
        self.assertNotIn("_recent_completions", metrics_source)
        self.assertNotIn("_broadcast_completion", metrics_source)
        self.assertNotIn("full_content", metrics_source)
        self.assertIn("r.aiter_raw()", metrics_source)

        intelligence = service_block("intelligence")
        locked = {
            "INTELLIGENCE_SCHEDULER_ENABLED": "0",
            "INTELLIGENCE_MEMORY_ENABLED": "0",
            "INTELLIGENCE_READ_ONLY": "1",
            "INTELLIGENCE_MULTIMODAL_ENABLED": "0",
            "INTELLIGENCE_MULTIMODAL_RING_ENABLED": "0",
            "INTELLIGENCE_MULTIMODAL_PILOT_ENABLED": "0",
            "INTELLIGENCE_MULTIMODAL_PILOT_AUTO_LABEL": "0",
        }
        for name, value in locked.items():
            self.assertIn(f'{name}: "{value}"', intelligence)
            self.assertNotIn(f"${{{name}", intelligence)

        standalone_deploy = read("tools/deploy-intelligence.ps1")
        for name, value in locked.items():
            self.assertIn(f'{name}: "{value}"', standalone_deploy)
            self.assertNotIn(f"{name}: `${{", standalone_deploy)

    def test_legacy_intelligence_is_absent_from_core_imports(self) -> None:
        core_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "stack/services/home-agent-core/app").glob("*.py")
        )
        self.assertNotRegex(core_sources, re.compile(r"hav[-_]intelligence|intelligence\.sqlite", re.I))


if __name__ == "__main__":
    unittest.main()
