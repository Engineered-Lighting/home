"""The runner's HA-host module list and its installer must not drift apart.

Step 19 stops Home Assistant; step 20 then runs the freeze and its observation
*on the HA host*. Three modules have to be there and byte-identical to the
pinned source. Two independently-maintained lists say which: the runner's
`REMOTE_HA_MODULES`, which verifies them, and `install-ha-operator-module.sh`,
which puts them there. Nothing cross-checked the two.

The cost of that was real. The installer shipped only the loader, so the freeze
observer on the Home Assistant host sat three revisions behind its pinned
source -- still shelling out to `ha core info` for a run-state key this
deployment does not return, which raises unconditionally -- while a readiness
audit recorded the file as "present". Presence is exactly what a stale copy
also satisfies, and the failure would have landed at step 20, with Home
Assistant already stopped.

These tests read both files as text rather than importing the runner, which
pulls in host-only transports.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "stack/home-agent-deploy/operator/phase3_activation_runner.py"
INSTALLER = ROOT / "stack/home-agent-deploy/install-ha-operator-module.sh"

# The three remote paths, spelled the way both files spell them.
REMOTE_PATHS = (
    "/config/home-agent-operator/migrate_legacy_identity.py",
    "/config/extended_openai_conversation/freeze_legacy_identity_semantics.py",
    "/config/extended_openai_conversation/"
    "collect_legacy_identity_freeze_observation.py",
)


def _runner() -> str:
    return RUNNER.read_text(encoding="utf-8")


def _installer() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def _installer_expanded() -> str:
    """The installer with its two root variables resolved.

    It writes remote paths as `$REMOTE_EOC_ROOT/<file>`, so comparing against
    the literal paths the runner uses means substituting the definitions rather
    than requiring the script to spell them out twice.
    """

    text = _installer()
    for name in ("REMOTE_OPERATOR_ROOT", "REMOTE_EOC_ROOT"):
        match = re.search(rf"^{name}=(\S+)$", text, re.MULTILINE)
        assert match, f"the installer no longer defines {name}"
        text = text.replace(f"${name}", match.group(1))
    return text


def _remote_module_table() -> str:
    source = _runner()
    assert "REMOTE_HA_MODULES = (" in source, (
        "the runner no longer names its HA-host module table"
    )
    return source.split("REMOTE_HA_MODULES = (", 1)[1].split("\n)", 1)[0]


def test_runner_verifies_every_module_step_twenty_runs() -> None:
    """All three modules are in the table the runner checks."""

    table = _remote_module_table()
    for symbol in ("OPERATOR_MODULE_SOURCE", "REMOTE_FREEZE", "REMOTE_OBSERVER"):
        assert symbol in table, f"{symbol} is not verified before step 20"
    assert "REMOTE_OPERATOR_MODULE" in table


def test_runner_checks_the_whole_table_not_one_module() -> None:
    """The check iterates; it used to hash a single hard-coded module."""

    source = _runner()
    body = source.split("def _require_remote_operator_module(", 1)[1].split(
        "\n    def ", 1
    )[0]
    assert "for source, remote in REMOTE_HA_MODULES:" in body, (
        "the remote-module check stopped iterating the table"
    )
    assert "hashlib.sha256(source.read_bytes())" in body
    assert 'ActivationPause("awaiting_ha_operator_module")' in body


@pytest.mark.parametrize("remote", REMOTE_PATHS)
def test_installer_installs_every_verified_module(remote: str) -> None:
    """Whatever the runner verifies, the installer must actually deploy.

    A module the runner checks but the installer never copies fails at step 20
    with Home Assistant already stopped -- which is how the observer went
    stale.
    """

    assert remote in _installer_expanded(), (
        f"{remote} is verified by the runner but never installed"
    )


@pytest.mark.parametrize("remote", REMOTE_PATHS)
def test_installer_sources_exist_in_the_repository(remote: str) -> None:
    """Each installed module names a real pinned source file."""

    installer = _installer()
    # Sources are written as "$ACTIVATION_ROOT/<repo-relative path>".
    relative = re.findall(r'"\$ACTIVATION_ROOT/([^"]+)"', installer)
    assert relative, "the installer stopped naming sources under ACTIVATION_ROOT"
    basename = remote.rsplit("/", 1)[1]
    matching = [path for path in relative if path.endswith(basename)]
    assert matching, f"no pinned source is installed to {remote}"
    for path in matching:
        assert (ROOT / path).is_file(), f"installer source is missing: {path}"


def test_installer_verifies_each_copy_by_digest() -> None:
    """Copying is not enough; each copy is compared after transfer."""

    installer = _installer()
    assert "sha256sum" in installer
    assert 'if [ "$expected" != "$actual" ]' in installer
    # One helper, applied to every module, rather than a copy per file.
    assert installer.count("install_module ") >= len(REMOTE_PATHS)
