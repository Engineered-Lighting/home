from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/home-agent-native-boundary.yml"


def test_native_boundary_gate_is_pinned_and_runs_on_windows() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "  contents: read" in text
    assert "    runs-on: windows-2025" in text
    assert "    timeout-minutes: 25" in text
    assert "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5" in text
    assert "uses: actions/checkout@v" not in text
    assert "uses: dtolnay/" not in text


def test_native_boundary_gate_checks_generated_ui_and_locked_rust() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "node tools/build-home-agent-panel.js --check" in text
    assert "cargo fmt --manifest-path app/src-tauri/Cargo.toml --check" in text
    assert "cargo test --manifest-path app/src-tauri/Cargo.toml --locked" in text
    assert 'if ($release -ne "1.88.0")' in text


def test_native_boundary_paths_cover_every_coupled_surface() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    for path in (
        '"app/src-tauri/**"',
        '"app/src/home-agent/**"',
        '"rust-toolchain.toml"',
        '"tests/home_agent/test_native_attestation_deployment_contract.py"',
        '"tests/home_agent/test_repository_contract.py"',
        '"tools/build-home-agent-panel.js"',
    ):
        assert text.count(path) == 2


if __name__ == "__main__":
    test_native_boundary_gate_is_pinned_and_runs_on_windows()
    test_native_boundary_gate_checks_generated_ui_and_locked_rust()
    test_native_boundary_paths_cover_every_coupled_surface()
