from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HA_MODULES = ROOT / "ha-config/extended_openai_conversation"
OPERATOR = ROOT / "stack/home-agent-deploy/operator"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(HA_MODULES))
sys.path.insert(0, str(OPERATOR))
try:
    fence = _load("legacy_identity_fence", HA_MODULES / "legacy_identity_fence.py")
    store_module = _load("identity_store", HA_MODULES / "identity_store.py")
    freezer = _load(
        "freeze_legacy_identity_semantics",
        HA_MODULES / "freeze_legacy_identity_semantics.py",
    )
    collector = _load(
        "collect_legacy_identity_freeze_observation",
        HA_MODULES / "collect_legacy_identity_freeze_observation.py",
    )
    migration = _load(
        "migrate_legacy_identity", OPERATOR / "migrate_legacy_identity.py"
    )
finally:
    sys.path.remove(str(OPERATOR))
    sys.path.remove(str(HA_MODULES))


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _frozen_database(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv(
        store_module.SEMANTIC_WRITES_MODE_ENV,
        store_module.LEGACY_MIGRATION_MODE,
    )
    database = tmp_path / "identity.db"
    operational = tmp_path / "identity-operational.db"
    store = store_module.IdentityStore(
        db_path=str(database), operational_db_path=str(operational)
    )
    store.setup()
    try:
        store.create_identity("Marcelo", relationship_type="me")
        store.create_identity(
            "Amelia",
            relationship_type="family_immediate",
            relationship_subrole="parent",
        )
    finally:
        store.close()
    assert freezer.freeze_database(database) == fence.SEMANTIC_WRITE_FENCE_GENERATION
    return database


def test_collector_proves_physical_fence_without_private_content(
    tmp_path, monkeypatch
) -> None:
    database = _frozen_database(tmp_path, monkeypatch)
    installation = tmp_path / ".source-installation-e5.json"
    monkeypatch.setattr(collector.sys, "platform", "linux")
    monkeypatch.setattr(collector.os, "geteuid", lambda: 0, raising=False)
    report = collector.collect(
        database_path=database,
        installation_path=installation,
        operator_root=OPERATOR,
        require_stopped=lambda: None,
        now=lambda: NOW,
    )
    assert report["contract"] == ("legacy-identity-physical-freeze-observation-v1")
    assert report["home_assistant_state"] == "stopped"
    assert report["process_lock_result"] == "exclusive"
    assert report["write_probe_result"] == "blocked"
    assert report["integrity_result"] == "passed"
    assert report["checkpoint_result"] == "complete"
    assert report["journal_result"] == "clean"
    assert report["source_plan_sha256"] == migration.load_plan(database).digest
    assert report["trigger_digest"] == fence.EXPECTED_TRIGGER_DIGEST
    assert "Marcelo" not in repr(report)
    assert "Amelia" not in repr(report)

    repeated = collector.collect(
        database_path=database,
        installation_path=installation,
        operator_root=OPERATOR,
        require_stopped=lambda: None,
        now=lambda: NOW,
    )
    assert repeated["source_installation_id"] == report["source_installation_id"]
    assert repeated["database_sha256"] == report["database_sha256"]


def test_collector_requires_cli_to_report_stopped() -> None:
    def running(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b'{"state":"running"}', stderr=b""
        )

    with pytest.raises(collector.FreezeObservationError, match="not stopped"):
        collector._require_home_assistant_stopped(runner=running)


def test_collector_rejects_source_installation_witness_tamper(
    tmp_path, monkeypatch
) -> None:
    database = _frozen_database(tmp_path, monkeypatch)
    installation = tmp_path / ".source-installation-e5.json"
    installation.write_text(
        '{"contract":"legacy-identity-source-installation-v1",'
        '"source_installation_id":"not-a-uuid"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(collector.sys, "platform", "linux")
    monkeypatch.setattr(collector.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(collector.FreezeObservationError, match="identifier is invalid"):
        collector.collect(
            database_path=database,
            installation_path=installation,
            operator_root=OPERATOR,
            require_stopped=lambda: None,
            now=lambda: NOW,
        )
