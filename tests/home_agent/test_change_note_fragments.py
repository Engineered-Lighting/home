"""Validate unreleased change-note fragments the way the release tool does.

`tools/release/check-change-notes.mjs` parses *every* file in
`changes/unreleased/` on each pull request, so one fragment with an unknown
`target:` fails the check for every subsequent PR, not just the one that
introduced it. That is exactly what happened: a fragment landed with
`target: deploy`, which reads like a valid choice -- the repo rules ask for a
fragment on "deploy-relevant work" -- but is not in the tool's allowlist. Three
PRs merged with the check red before anyone noticed.

The authority is the JavaScript. These tests read the allowlist out of it
rather than restating it, so a target added or removed there cannot drift from
what is asserted here.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNRELEASED = ROOT / "changes/unreleased"
RELEASE_LIB = ROOT / "tools/release/release-lib.mjs"


def _allowlist(name: str) -> set[str]:
    """Read a `new Set([...])` allowlist out of the release library."""

    source = RELEASE_LIB.read_text(encoding="utf-8")
    match = re.search(rf"{name}\s*=\s*new Set\(\[(.*?)\]\)", source, re.DOTALL)
    assert match, f"{name} is no longer a literal Set in {RELEASE_LIB.name}"
    values = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert values, f"{name} parsed as empty; the literal shape changed"
    return values


def _fragments() -> list[Path]:
    return sorted(UNRELEASED.glob("*.md"))


def _front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: no front matter"
    body = text.split("---\n", 2)
    assert len(body) >= 3, f"{path.name}: front matter is not closed"
    fields: dict[str, str] = {}
    for line in body[1].splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def test_there_are_fragments_to_check() -> None:
    assert _fragments(), "no unreleased fragments; the layout changed"


@pytest.mark.parametrize("fragment", _fragments(), ids=lambda path: path.name)
def test_fragment_target_is_one_the_release_tool_accepts(fragment: Path) -> None:
    valid = _allowlist("VALID_TARGETS")
    target = _front_matter(fragment).get("target")
    assert target, f"{fragment.name}: no target"
    assert target in valid, (
        f"{fragment.name}: target {target!r} is not one of {sorted(valid)}. "
        "check-change-notes.mjs parses every unreleased fragment, so this "
        "fails the check on every open pull request, not just this change."
    )


@pytest.mark.parametrize("fragment", _fragments(), ids=lambda path: path.name)
def test_fragment_type_is_one_the_release_tool_accepts(fragment: Path) -> None:
    valid = _allowlist("VALID_TYPES")
    kind = _front_matter(fragment).get("type")
    assert kind, f"{fragment.name}: no type"
    assert kind in valid, (
        f"{fragment.name}: type {kind!r} is not one of {sorted(valid)}"
    )


@pytest.mark.parametrize("fragment", _fragments(), ids=lambda path: path.name)
def test_fragment_has_a_title(fragment: Path) -> None:
    assert _front_matter(fragment).get("title"), f"{fragment.name}: no title"
