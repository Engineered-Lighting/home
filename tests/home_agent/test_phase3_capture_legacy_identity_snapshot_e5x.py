from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "stack/home-agent-deploy/operator/phase3_capture_legacy_identity_snapshot.py"
)
SPEC = importlib.util.spec_from_file_location("phase3_snapshot_e5x", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_capture_uses_only_fixed_private_and_remote_paths() -> None:
    assert module.HA_HOST == "192.168.0.125"
    assert module.HA_PORT == "22222"
    assert module.HA_DATABASE == ("/config/extended_openai_conversation/identity.db")
    assert module.SNAPSHOT_PATH == Path(
        "/srv/home-agent/private/phase3-identity/identity.db"
    )
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "argparse" not in source
    assert "shell=True" not in source
    assert "StrictHostKeyChecking=yes" in source
    assert "UserKnownHostsFile=" in source
    assert "OneDrive" in source


def test_capture_starts_ha_from_finally_and_refuses_existing_snapshot() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    capture = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "capture"
    )
    assert any(
        isinstance(node, ast.Try) and node.finalbody for node in ast.walk(capture)
    )
    source = ast.unparse(capture)
    assert "_start_home_assistant()" in source
    assert "_wait_home_assistant_ready()" in source
    assert "_prepare_private_root()" in source
    assert "os.replace(temporary, SNAPSHOT_PATH)" in source


def test_remote_snapshot_rejects_wal_journal_and_movement(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    digests = iter(["a" * 64, "a" * 64])

    def fake_remote(*arguments: str, timeout: int = 30) -> bytes:
        calls.append(arguments)
        if arguments[0] == "sh":
            return b"4096\n"
        raise AssertionError("unexpected remote call")

    monkeypatch.setattr(module, "_remote", fake_remote)
    monkeypatch.setattr(module, "_remote_sha256", lambda: next(digests))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    assert module._require_remote_stopped_database() == (4096, "a" * 64)
    shell_contract = calls[0][-1]
    assert 'test ! -e "' + module.HA_DATABASE + '-wal"' in shell_contract
    assert 'test ! -e "' + module.HA_DATABASE + '-journal"' in shell_contract

    monkeypatch.setattr(
        module,
        "_remote_sha256",
        lambda: next(iter(["a" * 64, "b" * 64])),
    )
    # Use an explicit iterator so the second call is distinct.
    changed = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(module, "_remote_sha256", lambda: next(changed))
    with pytest.raises(
        module.SnapshotCaptureError,
        match="changed during capture",
    ):
        module._require_remote_stopped_database()


def test_sqlite_verifier_requires_integrity_fk_and_schema_v1(tmp_path) -> None:
    import sqlite3

    database = tmp_path / "identity.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        "CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
        "INSERT INTO schema_meta VALUES('schema_version','1');"
    )
    connection.close()
    module._verify_sqlite(database)

    connection = sqlite3.connect(database)
    connection.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    connection.commit()
    connection.close()
    with pytest.raises(module.SnapshotCaptureError, match="unsupported"):
        module._verify_sqlite(database)


def test_capture_cli_is_content_minimized(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        module,
        "capture",
        lambda: {
            "contract": module.CONTRACT,
            "status": "captured",
            "private_snapshot_sha256": "a" * 64,
            "snapshot_bytes": 4096,
            "home_assistant_restarted": True,
            "writer_freeze_installed": False,
        },
    )
    monkeypatch.setattr(module.sys, "argv", [str(MODULE_PATH)])
    assert module.main() == 0
    output = capsys.readouterr().out
    assert "Marcelo" not in output
    assert "Amelia" not in output
    assert "/config/" not in output
    assert '"status":"captured"' in output
